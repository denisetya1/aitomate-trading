from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .cli import load_execution_snapshot
from .hermes import HermesCliValidator, HermesValidationError
from .learning import LearningStoreError, PostgresCliLearningStore
from .models import Decision
from .risk import RiskPolicy, check_candidate
from .strategy import StrategyConfig, generate_candidate
from .validation import validate_snapshot


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    temporary.replace(path)


def _send_telegram(
    sender_executable: str,
    message: str,
    *,
    runner: object = subprocess.run,
) -> bool:
    try:
        completed = runner(  # type: ignore[operator]
            [sender_executable, "send", "--to", "telegram", message],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


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
    trader_executable: str = "/home/deni/.local/bin/vission-trade",
) -> dict[str, object]:
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
    data = validate_snapshot(snapshot, now=now, max_quote_age=timedelta(seconds=5))
    if not data.ok:
        return {"status": "data_rejected", "reasons": data.reasons, "position_notifications": position_notifications}

    result = generate_candidate(snapshot, StrategyConfig(fixed_volume=0.01))
    if result.candidate is None:
        return {"status": "no_candidate", "reason": result.reason}
    candidate = result.candidate
    risk = check_candidate(candidate, snapshot, RiskPolicy(max_positions=1), now=now)
    if not risk.ok:
        return {"status": "risk_rejected", "reasons": risk.reasons}

    notification_state = state_path.with_name("candidate-notifications.json")
    candidate_message = (
        "Kandidat trading ditemukan\n"
        f"Setup: {candidate.setup_id}\n"
        f"{candidate.direction.value} 0.01 lot {candidate.symbol}\n"
        f"Entry sekitar {candidate.entry:.3f}\n"
        f"SL {candidate.stop_loss:.3f}\n"
        f"TP {candidate.take_profit:.3f}\n"
        f"Estimasi rugi maksimum {risk.estimated_loss:.2f} dalam mata uang akun\n"
        f"Reward/risk {risk.net_reward_risk:.2f}\n"
        "Status: menunggu keputusan Vission"
    )
    candidate_notified = notify_setup_once(
        candidate.setup_id,
        notification_state,
        "candidate_setup_ids",
        sender_executable,
        candidate_message,
    )

    validator = HermesCliValidator(executable=hermes_executable, profile="vission", timeout_seconds=90)
    try:
        validation = validator.validate(candidate, snapshot)
    except HermesValidationError as exc:
        decision_notified = notify_setup_once(
            candidate.setup_id,
            notification_state,
            "decision_setup_ids",
            sender_executable,
            "Keputusan Vission\n"
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

    decision_message = (
        "Keputusan Vission\n"
        f"Setup: {candidate.setup_id}\n"
        f"{validation.decision.value}\n"
        f"Probabilitas: {validation.probability:.1%}\n"
        f"Alasan: {validation.reason}"
    )
    decision_notified = notify_setup_once(
        candidate.setup_id,
        notification_state,
        "decision_setup_ids",
        sender_executable,
        decision_message,
    )
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
    last_execution = state.get("last_execution_at")
    if isinstance(last_execution, str):
        prior = datetime.fromisoformat(last_execution.replace("Z", "+00:00"))
        if now - prior < timedelta(minutes=15):
            return {
                "status": "execution_cooldown",
                "candidate_notified": candidate_notified,
                "decision_notified": decision_notified,
            }

    features = {
        "hour_utc": now.hour,
        "direction": candidate.direction.value,
        "spread_points": (snapshot.quote.ask - snapshot.quote.bid) / snapshot.symbol_spec.point,
        "open_positions": snapshot.open_positions,
        "pending_orders": snapshot.pending_orders,
        "net_reward_risk": risk.net_reward_risk,
    }
    try:
        probability = learning.assess(candidate, validation.probability, risk.net_reward_risk)
        learning.record_prediction(candidate, validation, risk, probability, features)
    except LearningStoreError as exc:
        return {"status": "learning_unavailable", "reason": str(exc)}
    if not probability.execute:
        return {
            "status": "probability_rejected",
            "raw_probability": validation.probability,
            "calibrated_probability": probability.calibrated_probability,
            "sample_size": probability.sample_size,
            "reason": probability.reason,
        }

    pending = {
        "setup_id": candidate.setup_id,
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(minutes=2)).isoformat(),
        "direction": candidate.direction.value,
        "stop_loss": candidate.stop_loss,
        "take_profit": candidate.take_profit,
        "entry_estimate": candidate.entry,
        "volume": 0.01,
    }
    _write_json(pending_path, pending)
    try:
        executed = subprocess.run(
            [trader_executable, "--confirm", "REAL"],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        pending_path.unlink(missing_ok=True)
        return {"status": "execution_failed", "reason": type(exc).__name__}
    pending_path.unlink(missing_ok=True)
    execution_text = executed.stdout.strip() or json.dumps(
        {"ok": False, "error": "empty_execution_result", "code": executed.returncode}
    )
    _write_json(state_path, {"last_execution_at": now.isoformat(), "setup_id": candidate.setup_id})
    message = (
        "Eksekusi otomatis REAL XAUUSDc\n"
        f"{candidate.direction.value} 0.01 lot\n"
        f"Entry sekitar {candidate.entry:.3f}\n"
        f"SL {candidate.stop_loss:.3f}\n"
        f"TP {candidate.take_profit:.3f}\n"
        f"Estimasi rugi maksimum {risk.estimated_loss:.2f} dalam mata uang akun\n"
        f"Reward/risk {risk.net_reward_risk:.2f}\n"
        f"Probabilitas AI {validation.probability:.1%}\n"
        f"Probabilitas terkalibrasi {probability.calibrated_probability:.1%}\n"
        f"Sampel hasil {probability.sample_size}\n"
        f"Vission: {validation.reason}\n"
        f"Hasil MT5: {execution_text}"
    )
    sent = subprocess.run(
        [sender_executable, "send", "--to", "telegram", message],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if sent.returncode != 0:
        return {
            "status": "notification_failed",
            "code": sent.returncode,
            "execution_code": executed.returncode,
            "execution_result": execution_text,
        }
    return {
        "status": "executed" if executed.returncode == 0 else "execution_rejected",
        "setup_id": candidate.setup_id,
        "execution_result": execution_text,
    }
