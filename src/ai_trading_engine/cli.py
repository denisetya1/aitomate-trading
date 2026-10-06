from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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


def main() -> None:
    parser = argparse.ArgumentParser(prog="trading-engine")
    subparsers = parser.add_subparsers(dest="command", required=True)
    doctor_parser = subparsers.add_parser("doctor", help="inspect a market snapshot without trading")
    doctor_parser.add_argument("--market-json", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "doctor":
        raise SystemExit(doctor(args.market_json))


if __name__ == "__main__":
    main()
