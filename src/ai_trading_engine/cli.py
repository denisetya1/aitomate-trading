from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .execution import FileBridgeExecutor, RealExecutionLocked
from .models import AccountState, Candle, Direction, MarketSnapshot, Quote, SymbolSpec, TradeCandidate
from .risk import RiskPolicy, check_candidate
from .validation import validate_snapshot


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def doctor(path: Path) -> int:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__}))
        return 2

    captured_at = _parse_time(payload.get("captured_at") or payload.get("timestamp") or payload.get("server_time"))
    now = datetime.now(timezone.utc)
    candle_counts = {
        key: len(value)
        for key, value in (payload.get("candles") or {}).items()
        if isinstance(value, list)
    }
    required_spec = {
        "tick_size",
        "tick_value",
        "volume_min",
        "volume_max",
        "volume_step",
        "stops_level_points",
        "filling_modes",
    }
    spec = payload.get("symbol_spec") or {}
    snapshot_age = (now - captured_at).total_seconds() if captured_at else None
    report = {
        "ok": snapshot_age is not None and -1 <= snapshot_age <= 5,
        "symbol": payload.get("symbol"),
        "snapshot_age_seconds": round(snapshot_age, 3) if snapshot_age is not None else None,
        "future_snapshot": snapshot_age is not None and snapshot_age < -1,
        "stale_snapshot": snapshot_age is None or snapshot_age > 5,
        "candle_counts": candle_counts,
        "open_position_count": len(payload.get("positions") or []),
        "pending_order_count": len(payload.get("orders") or []),
        "missing_symbol_spec_fields": sorted(required_spec.difference(spec)),
        "account_values_redacted": True,
        "execution_attempted": False,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def load_execution_snapshot(path: Path) -> MarketSnapshot:
    payload = json.loads(path.read_text(encoding="utf-8"))
    spec = payload["symbol_spec"]
    account = payload["account"]
    quote = payload["quote"]
    captured_at = datetime.fromtimestamp(float(quote["captured_at_msc"]) / 1000.0, timezone.utc)
    candles = {
        timeframe: tuple(
            Candle(
                open_time=datetime.fromtimestamp(float(item["open_time"]), timezone.utc),
                close_time=datetime.fromtimestamp(float(item["close_time"]), timezone.utc),
                open=float(item["open"]),
                high=float(item["high"]),
                low=float(item["low"]),
                close=float(item["close"]),
                volume=float(item.get("tick_volume", 0)),
            )
            for item in items
        )
        for timeframe, items in (payload.get("candles") or {}).items()
    }
    return MarketSnapshot(
        snapshot_id=str(payload["snapshot_id"]),
        captured_at=captured_at,
        symbol=str(payload["symbol"]),
        quote=Quote(captured_at, float(quote["bid"]), float(quote["ask"])),
        symbol_spec=SymbolSpec(
            symbol=str(payload["symbol"]),
            digits=int(spec["digits"]),
            point=float(spec["point"]),
            tick_size=float(spec["tick_size"]),
            tick_value=float(spec["tick_value"]),
            contract_size=float(spec["contract_size"]),
            volume_min=float(spec["volume_min"]),
            volume_max=float(spec["volume_max"]),
            volume_step=float(spec["volume_step"]),
            stops_level_points=int(spec.get("stops_level_points", 0)),
            freeze_level_points=int(spec.get("freeze_level_points", 0)),
        ),
        account=AccountState(
            equity=float(account["equity"]),
            day_start_equity=float(account["equity"]),
            realized_pnl_today=0.0,
        ),
        candles=candles,
        open_positions=len(payload.get("positions") or []),
        pending_orders=len(payload.get("orders") or []),
    )


def submit_real_order(
    market_path: Path,
    bridge_directory: Path,
    direction: Direction,
    stop_loss: float,
    take_profit: float,
    confirmation: str,
) -> int:
    try:
        snapshot = load_execution_snapshot(market_path)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": f"invalid_snapshot:{type(exc).__name__}"}))
        return 2

    now = datetime.now(timezone.utc)
    data_check = validate_snapshot(snapshot, now=now, max_quote_age=timedelta(seconds=5))
    if not data_check.ok:
        print(json.dumps({"ok": False, "error": "data_rejected", "reasons": data_check.reasons}))
        return 3

    entry = snapshot.quote.ask if direction is Direction.BUY else snapshot.quote.bid
    seed = f"{snapshot.snapshot_id}|{direction.value}|{stop_loss:.8f}|{take_profit:.8f}"
    setup_id = hashlib.sha256(seed.encode("ascii")).hexdigest()[:20]
    candidate = TradeCandidate(
        setup_id=setup_id,
        strategy_version="vission-confirmed-v1",
        snapshot_id=snapshot.snapshot_id,
        created_at=now,
        expires_at=now + timedelta(minutes=2),
        symbol=snapshot.symbol,
        direction=direction,
        entry=entry,
        stop_loss=stop_loss,
        take_profit=take_profit,
        volume=0.01,
    )
    risk = check_candidate(
        candidate,
        snapshot,
        RiskPolicy(max_positions=1, max_spread_points=400.0, min_net_reward_risk=1.5),
        now=now,
    )
    if not risk.ok:
        print(json.dumps({"ok": False, "error": "risk_rejected", "reasons": risk.reasons}))
        return 4

    try:
        executor = FileBridgeExecutor(bridge_directory, confirmation=confirmation)
    except RealExecutionLocked as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 5
    receipt = executor.submit(f"{setup_id}:entry", candidate)
    if not receipt.accepted:
        print(json.dumps({"ok": False, "setup_id": setup_id, "status": receipt.message, "volume": 0.01}))
        return 6

    receipt_path = bridge_directory / "receipt.json"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            broker_receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            time.sleep(0.25)
            continue
        if broker_receipt.get("request_id") == receipt.request_id:
            accepted = broker_receipt.get("accepted") is True
            print(
                json.dumps(
                    {
                        "ok": accepted,
                        "setup_id": setup_id,
                        "status": broker_receipt.get("status"),
                        "order_ticket": broker_receipt.get("order_ticket"),
                        "deal_ticket": broker_receipt.get("deal_ticket"),
                        "volume": 0.01,
                    }
                )
            )
            return 0 if accepted else 7
        time.sleep(0.25)
    print(json.dumps({"ok": True, "setup_id": setup_id, "status": "submitted_waiting_receipt", "volume": 0.01}))
    return 0


def confirm_pending_order(market_path: Path, bridge_directory: Path, pending_path: Path, confirmation: str) -> int:
    try:
        pending = json.loads(pending_path.read_text(encoding="utf-8"))
        expires_at = datetime.fromisoformat(str(pending["expires_at"]).replace("Z", "+00:00"))
        direction = Direction(str(pending["direction"]))
        stop_loss = float(pending["stop_loss"])
        take_profit = float(pending["take_profit"])
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": f"invalid_or_missing_pending:{type(exc).__name__}"}))
        return 2
    if datetime.now(timezone.utc) >= expires_at:
        pending_path.unlink(missing_ok=True)
        print(json.dumps({"ok": False, "error": "confirmation_expired"}))
        return 3
    result = submit_real_order(
        market_path,
        bridge_directory,
        direction,
        stop_loss,
        take_profit,
        confirmation,
    )
    if result == 0:
        pending_path.unlink(missing_ok=True)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(prog="trading-engine")
    subparsers = parser.add_subparsers(dest="command", required=True)
    doctor_parser = subparsers.add_parser("doctor", help="inspect a market snapshot without trading")
    doctor_parser.add_argument("--market-json", type=Path, required=True)
    real_parser = subparsers.add_parser("real-order", help="submit one confirmed 0.01-lot real order")
    real_parser.add_argument("--market-json", type=Path, required=True)
    real_parser.add_argument("--bridge-directory", type=Path, required=True)
    real_parser.add_argument("--direction", choices=["BUY", "SELL"], required=True)
    real_parser.add_argument("--stop-loss", type=float, required=True)
    real_parser.add_argument("--take-profit", type=float, required=True)
    real_parser.add_argument("--confirm", required=True)
    pending_parser = subparsers.add_parser("confirm-pending", help="confirm the latest Vission preview")
    pending_parser.add_argument("--market-json", type=Path, required=True)
    pending_parser.add_argument("--bridge-directory", type=Path, required=True)
    pending_parser.add_argument("--pending-json", type=Path, required=True)
    pending_parser.add_argument("--confirm", required=True)
    args = parser.parse_args()
    if args.command == "doctor":
        raise SystemExit(doctor(args.market_json))
    if args.command == "real-order":
        raise SystemExit(
            submit_real_order(
                args.market_json,
                args.bridge_directory,
                Direction(args.direction),
                args.stop_loss,
                args.take_profit,
                args.confirm,
            )
        )
    if args.command == "confirm-pending":
        raise SystemExit(
            confirm_pending_order(
                args.market_json,
                args.bridge_directory,
                args.pending_json,
                args.confirm,
            )
        )


if __name__ == "__main__":
    main()
