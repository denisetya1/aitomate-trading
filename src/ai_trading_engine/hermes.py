from __future__ import annotations

import json
import subprocess
import tempfile
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .models import Decision, MarketSnapshot, TradeCandidate, ValidationResult


class HermesValidationError(RuntimeError):
    pass


def _extract_json(text: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    for index, character in enumerate(text):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise HermesValidationError("Hermes response contains no JSON object")


def parse_validation(text: str, candidate: TradeCandidate, now: datetime | None = None) -> ValidationResult:
    now = now or datetime.now(timezone.utc)
    payload = _extract_json(text)
    try:
        decision = Decision(str(payload["decision"]).upper())
        setup_id = str(payload["setup_id"])
        reason = str(payload["reason"]).strip()
    except (KeyError, ValueError, TypeError) as exc:
        raise HermesValidationError("Invalid Hermes validation schema") from exc
    if setup_id != candidate.setup_id:
        raise HermesValidationError("Hermes setup_id does not match candidate")
    if not reason or len(reason) > 500:
        raise HermesValidationError("Hermes reason is empty or too long")
    if now >= candidate.expires_at:
        raise HermesValidationError("Candidate expired before Hermes response")
    forbidden = {"entry", "stop_loss", "take_profit", "volume", "symbol", "direction"}
    if forbidden.intersection(payload):
        raise HermesValidationError("Hermes attempted to modify trade parameters")
    return ValidationResult(decision, setup_id, reason, now)


class HermesCliValidator:
    def __init__(
        self,
        executable: str = "hermes",
        profile: str = "vission",
        timeout_seconds: int = 90,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.executable = executable
        self.profile = profile
        self.timeout_seconds = timeout_seconds
        self.runner = runner
        self.clock = clock

    def validate(self, candidate: TradeCandidate, snapshot: MarketSnapshot) -> ValidationResult:
        prompt = self._prompt(candidate, snapshot)
        with tempfile.TemporaryDirectory(prefix="hermes-validation-") as temp_dir:
            usage_file = Path(temp_dir) / "usage.json"
            command = [
                self.executable,
                "-p",
                self.profile,
                "--safe-mode",
                "--usage-file",
                str(usage_file),
                "-z",
                prompt,
            ]
            try:
                completed = self.runner(
                    command,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise HermesValidationError("Hermes invocation failed") from exc
        if completed.returncode != 0:
            raise HermesValidationError(f"Hermes exited with code {completed.returncode}")
        return parse_validation(completed.stdout, candidate, self.clock())

    @staticmethod
    def _prompt(candidate: TradeCandidate, snapshot: MarketSnapshot) -> str:
        payload = {
            "candidate": _json_safe(asdict(candidate)),
            "market": {
                "snapshot_id": snapshot.snapshot_id,
                "captured_at": snapshot.captured_at.isoformat(),
                "symbol": snapshot.symbol,
                "bid": snapshot.quote.bid,
                "ask": snapshot.quote.ask,
                "open_positions": snapshot.open_positions,
                "pending_orders": snapshot.pending_orders,
            },
        }
        return (
            "You validate a fully specified deterministic trade candidate. "
            "Do not propose or modify parameters. Return exactly one JSON object with keys "
            'decision (APPROVE, REJECT, or ABSTAIN), setup_id, and reason. '
            f"Input: {json.dumps(payload, separators=(',', ':'))}"
        )


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "value"):
        return value.value
    return value
