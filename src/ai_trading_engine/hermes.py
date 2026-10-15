from __future__ import annotations

import json
import subprocess
import tempfile
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
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
        probability = float(payload["probability"])
    except (KeyError, ValueError, TypeError) as exc:
        raise HermesValidationError("Invalid Hermes validation schema") from exc
    if setup_id != candidate.setup_id:
        raise HermesValidationError("Hermes setup_id does not match candidate")
    if not reason or len(reason) > 500:
        raise HermesValidationError("Hermes reason is empty or too long")
    if not 0.0 <= probability <= 1.0:
        raise HermesValidationError("Hermes probability is outside 0..1")
    if now >= candidate.expires_at:
        raise HermesValidationError("Candidate expired before Hermes response")
    forbidden = {"entry", "stop_loss", "take_profit", "volume", "symbol", "direction"}
    if forbidden.intersection(payload):
        raise HermesValidationError("Hermes attempted to modify trade parameters")
    return ValidationResult(decision, setup_id, reason, probability, now)


class HermesCliValidator:
    def __init__(
        self,
        executable: str = "hermes",
        profile: str = "vission",
        model: str = "gpt-6-luna",
        timeout_seconds: int = 90,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.executable = executable
        self.profile = profile
        self.model = model
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
                "-m",
                self.model,
                "--reasoning",
                "low",
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
            except subprocess.TimeoutExpired as exc:
                raise HermesValidationError(
                    f"Hermes validation timed out after {self.timeout_seconds} seconds"
                ) from exc
            except OSError as exc:
                raise HermesValidationError(
                    f"Hermes could not be started: {exc.__class__.__name__}"
                ) from exc
        if completed.returncode != 0:
            raise HermesValidationError(f"Hermes exited with code {completed.returncode}")
        now = self.clock()
        age = now - snapshot.captured_at
        if age > timedelta(seconds=30) or age < timedelta(seconds=-1):
            raise HermesValidationError("AI context exceeds 30 seconds or is future-dated")
        return parse_validation(completed.stdout, candidate, now)

    @staticmethod
    def _prompt(candidate: TradeCandidate, snapshot: MarketSnapshot) -> str:
        candle_limits = {"M5": 30, "M15": 60, "H1": 60, "H4": 12, "D1": 5}
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
                "candle_format": ["close_time", "open", "high", "low", "close", "volume"],
                "candles": {
                    tf: [
                        [
                            candle.close_time.isoformat(),
                            candle.open,
                            candle.high,
                            candle.low,
                            candle.close,
                            candle.volume,
                        ]
                        for candle in snapshot.closed_candles(tf)[-candle_limits.get(tf, 10):]
                    ]
                    for tf in snapshot.candles
                },
            },
        }
        return (
            "You validate a fully specified deterministic trade candidate. "
            "Do not propose or modify parameters. Return exactly one JSON object with keys "
            "This is setup-context validation, not execution authorization. Context may be up to 30 seconds old at decision time. "
            "The engine enforces this age and separately reloads quotes no older than 5 seconds, "
            "revalidates the same setup, entry range and risk before execution. A changed snapshot ID alone is not a reason to abstain. "
            "The serialized Input is the complete and authoritative evidence for this validation. "
            "Do not access or require any filesystem snapshot, chart screenshot, external tool, or additional data. "
            "The deterministic adaptive price-action engine has already classified the setup as either a trend pullback "
            "or a range liquidity-sweep reversal. It has confirmed the required H1 regime, M15 context, M5 rejection, "
            "reference level, structure stop, target and risk checks. The strategy type is in candidate.strategy_version. "
            "Use only the supplied candles to assess whether that specific setup remains coherent and estimate TP-before-SL probability. "
            "Do not require EMA, RSI, ATR, screenshots, news, or another indicator. "
            "Use the supplied closed candles; abstain only when those supplied market facts conflict or are insufficient. "
            "decision (APPROVE, REJECT, or ABSTAIN), setup_id, probability (0..1), and reason. "
            "Keep reason under 240 characters. "
            "Probability means the estimated chance that TP is reached before SL; do not inflate it. "
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
