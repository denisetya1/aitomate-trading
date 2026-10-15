from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .cli import load_execution_snapshot
from .execution import FileBridgeExecutor, RealExecutionLocked
from .hermes import HermesCliValidator, HermesValidationError, _json_safe
from .learning import LearningStoreError, PostgresCliLearningStore
from .models import Decision, TradeCandidate
from .paper import has_open_paper_trade, open_paper_trade, settle_paper_trades
from .risk import RiskPolicy, check_candidate
from .strategy import StrategyConfig, generate_candidate
from .validation import validate_snapshot
from .revalidation import revalidate_approved
from dataclasses import asdict, replace


def _apply_vission_execution_decision(probability, decision: Decision):
    approved = decision is Decision.APPROVE
    return replace(
        probability,
        execute=approved,
        reason=(
            "vission_approve_direct_execution"
            if approved
            else f"vission_{decision.value.lower()}_blocked"
        ),
    )


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    temporary.replace(path)


def _symbol_has_exposure(payload: dict[str, object], symbol: str) -> bool:
    positions = payload.get("positions") or []
    orders = payload.get("orders") or []
    return any(str(item.get("symbol", "")) == symbol for item in [*positions, *orders])


def _execute_real(candidate: TradeCandidate, bridge_directory: Path) -> dict[str, object]:
    try:
        executor = FileBridgeExecutor(bridge_directory, confirmation="REAL", fixed_volume=0.01)
        receipt = executor.submit(f"{candidate.setup_id}:entry", candidate)
    except (OSError, ValueError, RealExecutionLocked) as exc:
        return {"ok": False, "status": f"bridge_submit_failed:{type(exc).__name__}"}
    if not receipt.accepted:
        return {"ok": False, "status": receipt.message}

    receipt_path = bridge_directory / "receipt.json"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            broker = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            time.sleep(0.25)
            continue
        if broker.get("request_id") == receipt.request_id:
            return {
                "ok": broker.get("accepted") is True,
                "status": str(broker.get("status", "unknown")),
                "order_ticket": broker.get("order_ticket"),
                "deal_ticket": broker.get("deal_ticket"),
            }
        time.sleep(0.25)
    return {"ok": True, "status": "submitted_waiting_receipt"}


def _send_telegram(
    sender_executable: str,
    message: str,
    *,
    runner: object = subprocess.run,
) -> bool:
    try:
        completed = runner(  # type: ignore[operator]
            [sender_executable, "send", "--to", os.environ.get("AITOMATE_TELEGRAM_TARGET", "telegram"), message],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def _notify_approved_not_executed(
    sender_executable: str,
    candidate: TradeCandidate,
    reason: str,
) -> bool:
    return _send_telegram(
        sender_executable,
        "APPROVE tidak dieksekusi\n"
        f"Pair: {candidate.symbol}\n"
        f"Setup: {candidate.setup_id}\n"
        f"Alasan: {reason}",
    )


def notify_setup_once(
    setup_id: str,
    state_path: Path,
    state_key: str,
    sender_executable: str,
    message: str,
    *,
    runner: object = subprocess.run,
) -> bool:
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        state = {}
    notified = [str(value) for value in state.get(state_key, [])]
    if setup_id in notified:
        return True
    if not _send_telegram(sender_executable, message, runner=runner):
        return False
    notified.append(setup_id)
    state[state_key] = notified[-100:]
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    _write_json(state_path, state)
    return True


def notify_new_positions(
    positions: list[dict[str, object]],
    state_path: Path,
    sender_executable: str,
    *,
    runner: object = subprocess.run,
) -> dict[str, object]:
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        state = {}
    previous = {str(ticket) for ticket in state.get("notified_tickets", [])}
    current = {str(item.get("ticket")) for item in positions if item.get("ticket") is not None}
    notified = previous.intersection(current)
    sent_tickets: list[str] = []
    failed_tickets: list[str] = []

    for position in positions:
        ticket = str(position.get("ticket", ""))
        if not ticket or ticket in notified:
            continue
        direction = "BUY" if int(position.get("type", -1)) == 0 else "SELL"
        source = "Vission" if int(position.get("magic", 0)) == 2601001 else "Manual/EA lain"
        message = (
            "Posisi baru terbuka\n"
            f"Sumber: {source}\n"
            f"Tiket: {ticket}\n"
            f"{direction} {float(position.get('volume', 0)):.2f} lot {position.get('symbol', '')}\n"
            f"Harga buka: {float(position.get('price_open', 0)):.3f}\n"
            f"SL: {float(position.get('sl', 0)):.3f}\n"
            f"TP: {float(position.get('tp', 0)):.3f}\n"
            f"Floating: {float(position.get('profit', 0)):.2f}"
        )
        if _send_telegram(sender_executable, message, runner=runner):
            notified.add(ticket)
            sent_tickets.append(ticket)
        else:
            failed_tickets.append(ticket)

    _write_json(
        state_path,
        {
            "notified_tickets": sorted(notified),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return {"sent": sent_tickets, "failed": failed_tickets}


def scan_once(
    market_path: Path,
    state_path: Path,
    pending_path: Path,
    *,
    hermes_executable: str = "/home/deni/.local/bin/hermes",
    sender_executable: str = "/home/deni/.local/bin/vission",
) -> dict[str, object]:
    pending_path.unlink(missing_ok=True)
    now = datetime.now(timezone.utc)
    try:
        raw_snapshot = json.loads(market_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"status": "snapshot_unavailable", "reason": type(exc).__name__}
    position_notifications = notify_new_positions(
        raw_snapshot.get("positions") or [],
        state_path.with_name("open-positions.json"),
        sender_executable,
    )
    learning = PostgresCliLearningStore()
    open_position_ids = [
        int(item.get("position_id", item.get("ticket", 0)))
        for item in (raw_snapshot.get("positions") or [])
        if item.get("position_id", item.get("ticket")) is not None
    ]
    try:
        learning.ingest_deals(raw_snapshot.get("deals") or [], open_position_ids)
    except LearningStoreError as exc:
        return {"status": "learning_unavailable", "reason": str(exc), "position_notifications": position_notifications}
    snapshot = load_execution_snapshot(market_path)
    execution_mode = os.environ.get("AITOMATE_EXECUTION_MODE", "PAPER").strip().upper()
    if execution_mode not in {"PAPER", "REAL"}:
        return {"status": "configuration_error", "reason": "invalid_execution_mode"}
    if _symbol_has_exposure(raw_snapshot, snapshot.symbol):
        return {"status": "symbol_exposure_open", "symbol": snapshot.symbol}
    now = datetime.now(timezone.utc)
    data = validate_snapshot(snapshot, now=now, max_quote_age=timedelta(seconds=30))
    if not data.ok:
        return {"status": "data_rejected", "reasons": data.reasons, "position_notifications": position_notifications}


    paper_path = state_path.with_name(f"paper-trades-{snapshot.symbol}.json")
    closed_paper = settle_paper_trades(paper_path, snapshot) if execution_mode == "PAPER" else []
    try:
        for outcome in closed_paper:
            learning.record_paper_outcome(
                str(outcome["setup_id"]),
                str(outcome["closed_at"]),
                float(outcome["pnl"]),
                float(outcome["r_multiple"]),
                bool(outcome["won"]),
            )
    except LearningStoreError as exc:
        return {"status": "learning_unavailable", "reason": str(exc)}

    result = generate_candidate(snapshot, StrategyConfig(fixed_volume=0.01))
    if result.candidate is None:
        return {"status": "no_candidate", "reason": result.reason}
    candidate = result.candidate
    reference_level = result.reference_level
    risk_policy = RiskPolicy(
        max_positions=snapshot.open_positions + snapshot.pending_orders + 1,
        daily_loss_limit_pct=1.0,
        max_spread_points=400.0,
        min_net_reward_risk=1.3,
    )
    risk = check_candidate(
        candidate,
        snapshot,
        risk_policy,
        now=now,
    )
    if not risk.ok:
        return {"status": "risk_rejected", "reasons": risk.reasons}

    notification_state = state_path.with_name("candidate-notifications.json")
    candidate_message = (
        "Kandidat trading ditemukan\n"
        f"Strategi: {candidate.strategy_version}\n"
        f"Setup: {candidate.setup_id}\n"
        f"{candidate.direction.value} 0.01 lot {candidate.symbol}\n"
        f"Entry sekitar {candidate.entry:.3f}\n"
        f"SL {candidate.stop_loss:.3f}\n"
        f"TP {candidate.take_profit:.3f}\n"
        f"Estimasi rugi maksimum {risk.estimated_loss:.2f} dalam mata uang akun\n"
        f"Reward/risk {risk.net_reward_risk:.2f}\n"
        f"Status: menunggu validasi Vission; mode {execution_mode}"
    )
    candidate_notified = notify_setup_once(
        candidate.setup_id,
        notification_state,
        "candidate_setup_ids",
        sender_executable,
        candidate_message,
    )

    validator = HermesCliValidator(
        executable=hermes_executable,
        profile="vission",
        model="gpt-5.6-sol",
        timeout_seconds=25,
    )
    try:
        validation = validator.validate(candidate, snapshot)
    except HermesValidationError as exc:
        decision_notified = notify_setup_once(
            candidate.setup_id,
            notification_state,
            "decision_setup_ids",
            sender_executable,
            "Keputusan Vission\n"
            f"Pair: {candidate.symbol}\n"
            f"Setup: {candidate.setup_id}\n"
            "ABSTAIN\n"
            f"Alasan: {exc}",
        )
        return {
            "status": "ai_abstained",
            "reason": str(exc),
            "candidate_notified": candidate_notified,
            "decision_notified": decision_notified,
        }

    decision_notified = notify_setup_once(
        candidate.setup_id,
        notification_state,
        "decision_setup_ids",
        sender_executable,
        "Keputusan Vission\n"
        f"Pair: {candidate.symbol}\n"
        f"Setup: {candidate.setup_id}\n"
        f"{validation.decision.value}\n"
        f"Probabilitas: {validation.probability:.1%}\n"
        f"Alasan: {validation.reason}",
    )
    now = datetime.now(timezone.utc)
    features = {
        "hour_utc": now.hour,
        "direction": candidate.direction.value,
        "spread_points": (snapshot.quote.ask - snapshot.quote.bid) / snapshot.symbol_spec.point,
        "open_positions": snapshot.open_positions,
        "pending_orders": snapshot.pending_orders,
        "net_reward_risk": risk.net_reward_risk,
        "execution_mode": f"{execution_mode.lower()}_vission_validated",
        "setup_type": result.setup_type,
        "reference_level": reference_level,
        "candidate": _json_safe(asdict(candidate)),
        "market_context": {
            "snapshot_id": snapshot.snapshot_id,
            "captured_at": snapshot.captured_at.isoformat(),
            "bid": snapshot.quote.bid,
            "ask": snapshot.quote.ask,
            "candles": {
                timeframe: [
                    [
                        candle.close_time.isoformat(),
                        candle.open,
                        candle.high,
                        candle.low,
                        candle.close,
                        candle.volume,
                    ]
                    for candle in snapshot.closed_candles(timeframe)[-limit:]
                ]
                for timeframe, limit in {"M5": 30, "M15": 60, "H1": 12, "H4": 6, "D1": 3}.items()
            },
        },
    }

    try:
        probability = learning.assess(candidate, validation.probability, risk.net_reward_risk)
        probability = _apply_vission_execution_decision(probability, validation.decision)
        learning.record_prediction(candidate, validation, risk, probability, features)
    except LearningStoreError as exc:
        return {"status": "learning_unavailable", "reason": str(exc)}
    if validation.decision is not Decision.APPROVE:
        return {
            "status": f"ai_{validation.decision.value.lower()}",
            "reason": validation.reason,
            "candidate_notified": candidate_notified,
            "decision_notified": decision_notified,
        }
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        state = {}
    cooldown_key = "last_real_entry_at" if execution_mode == "REAL" else "last_paper_entry_at"
    last_entry = state.get(cooldown_key)
    if isinstance(last_entry, str):
        prior = datetime.fromisoformat(last_entry.replace("Z", "+00:00"))
        if now - prior < timedelta(minutes=15):
            return {
                "status": f"{execution_mode.lower()}_entry_cooldown",
                "candidate_notified": candidate_notified,
                "paper_outcomes_recorded": len(closed_paper),
            }

    try:
        fresh_snapshot = load_execution_snapshot(market_path)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        reason = "snapshot_unavailable"
        _notify_approved_not_executed(sender_executable, candidate, reason)
        return {"status": f"{execution_mode.lower()}_rejected", "reason": reason}
    now = datetime.now(timezone.utc)
    execution_policy = RiskPolicy(
        max_positions=fresh_snapshot.open_positions + fresh_snapshot.pending_orders + 1,
        daily_loss_limit_pct=1.0,
        max_spread_points=400.0,
        min_net_reward_risk=1.3,
    )
    refreshed = revalidate_approved(candidate, fresh_snapshot, now=now, policy=execution_policy)
    if refreshed.candidate is None:
        _notify_approved_not_executed(sender_executable, candidate, refreshed.reason)
        return {"status": f"{execution_mode.lower()}_rejected", "reason": refreshed.reason}
    candidate = refreshed.candidate

    try:
        fresh_raw = json.loads(market_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        reason = "snapshot_unavailable"
        _notify_approved_not_executed(sender_executable, candidate, reason)
        return {"status": f"{execution_mode.lower()}_rejected", "reason": reason}
    if _symbol_has_exposure(fresh_raw, candidate.symbol):
        _notify_approved_not_executed(sender_executable, candidate, "symbol_exposure_open")
        return {"status": "symbol_exposure_open", "symbol": candidate.symbol}

    if execution_mode == "REAL":
        execution = _execute_real(candidate, market_path.parent)
        if execution.get("ok") is not True:
            _send_telegram(
                sender_executable,
                "Order REAL ditolak\n"
                f"{candidate.direction.value} 0.01 lot {candidate.symbol}\n"
                f"Status: {execution.get('status', 'unknown')}",
            )
            return {
                "status": "real_rejected",
                "setup_id": candidate.setup_id,
                "broker_status": execution.get("status"),
                "order_ticket": execution.get("order_ticket"),
                "deal_ticket": execution.get("deal_ticket"),
            }
        _write_json(state_path, {cooldown_key: now.isoformat(), "setup_id": candidate.setup_id})
        broker_status = str(execution.get("status", "submitted"))
        sent = _send_telegram(
            sender_executable,
            "Order REAL price action adaptif dikirim\n"
            f"Strategi: {candidate.strategy_version}\n"
            f"{candidate.direction.value} 0.01 lot {candidate.symbol}\n"
            f"Entry {candidate.entry:.5f}\n"
            f"SL {candidate.stop_loss:.5f}\n"
            f"TP {candidate.take_profit:.5f}\n"
            f"Status broker: {broker_status}\n"
            f"Probabilitas Vission {validation.probability:.1%}\n"
            f"Vission: {validation.reason}",
        )
        return {
            "status": "real_executed" if broker_status == "executed" else "real_submitted",
            "setup_id": candidate.setup_id,
            "notification_sent": sent,
            "broker_status": broker_status,
            "order_ticket": execution.get("order_ticket"),
            "deal_ticket": execution.get("deal_ticket"),
        }

    if has_open_paper_trade(paper_path):
        return {"status": "paper_position_open", "setup_id": candidate.setup_id}
    if not open_paper_trade(
        paper_path,
        candidate,
        risk,
        opened_at=now,
        breakout_level=None,
        max_holding_minutes=30,
    ):
        return {"status": "paper_position_open", "setup_id": candidate.setup_id}

    _write_json(state_path, {cooldown_key: now.isoformat(), "setup_id": candidate.setup_id})
    message = (
        "Paper trade price action adaptif dibuka\n"
        f"Strategi: {candidate.strategy_version}\n"
        f"{candidate.direction.value} 0.01 lot {candidate.symbol}\n"
        f"Entry simulasi {candidate.entry:.3f}\n"
        f"SL {candidate.stop_loss:.3f}\n"
        f"TP {candidate.take_profit:.3f}\n"
        f"Estimasi rugi {risk.estimated_loss:.2f} dalam mata uang akun\n"
        f"Reward/risk {risk.net_reward_risk:.2f}\n"
        f"Probabilitas Vission {validation.probability:.1%}\n"
        f"Vission: {validation.reason}\n"
        "Mode: PAPER - tidak ada order yang dikirim ke MT5\n"
        "Hasil simulasi akan disimpan untuk learning Vission"
    )
    sent = _send_telegram(sender_executable, message)
    return {
        "status": "paper_opened",
        "setup_id": candidate.setup_id,
        "notification_sent": sent,
        "paper_outcomes_recorded": len(closed_paper),
    }
