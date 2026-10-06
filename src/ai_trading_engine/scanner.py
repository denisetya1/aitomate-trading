from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .cli import load_execution_snapshot
from .hermes import HermesCliValidator, HermesValidationError
from .models import Decision
from .risk import RiskPolicy, check_candidate
from .strategy import StrategyConfig, generate_candidate
from .validation import validate_snapshot


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    temporary.replace(path)


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
    snapshot = load_execution_snapshot(market_path)
    data = validate_snapshot(snapshot, now=now, max_quote_age=timedelta(seconds=5))
    if not data.ok:
        return {"status": "data_rejected", "reasons": data.reasons}

    result = generate_candidate(snapshot, StrategyConfig(fixed_volume=0.01))
    if result.candidate is None:
        return {"status": "no_candidate", "reason": result.reason}
    candidate = result.candidate
    risk = check_candidate(candidate, snapshot, RiskPolicy(max_positions=1), now=now)
    if not risk.ok:
        return {"status": "risk_rejected", "reasons": risk.reasons}

    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        state = {}
    last_execution = state.get("last_execution_at")
    if isinstance(last_execution, str):
        prior = datetime.fromisoformat(last_execution.replace("Z", "+00:00"))
        if now - prior < timedelta(minutes=15):
            return {"status": "execution_cooldown"}

    validator = HermesCliValidator(executable=hermes_executable, profile="vission", timeout_seconds=90)
    try:
        validation = validator.validate(candidate, snapshot)
    except HermesValidationError as exc:
        return {"status": "ai_abstained", "reason": str(exc)}
    if validation.decision is not Decision.APPROVE:
        return {"status": f"ai_{validation.decision.value.lower()}", "reason": validation.reason}

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
