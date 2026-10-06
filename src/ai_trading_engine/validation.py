from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .models import MarketSnapshot


@dataclass(frozen=True)
class DataCheck:
    ok: bool
    reasons: tuple[str, ...]


def validate_snapshot(
    snapshot: MarketSnapshot,
    *,
    now: datetime | None = None,
    max_quote_age: timedelta = timedelta(seconds=5),
    required_timeframes: tuple[str, ...] = ("M5", "M15", "H1"),
) -> DataCheck:
    now = now or datetime.now(timezone.utc)
    reasons: list[str] = []
    spec = snapshot.symbol_spec

    if snapshot.symbol != spec.symbol:
        reasons.append("symbol_spec_mismatch")
    if snapshot.quote.ask <= snapshot.quote.bid:
        reasons.append("invalid_quote")
    if now - snapshot.quote.captured_at > max_quote_age:
        reasons.append("stale_quote")
    if snapshot.quote.captured_at > now + timedelta(seconds=1):
        reasons.append("future_quote")
    if min(spec.point, spec.tick_size, spec.tick_value, spec.volume_step) <= 0:
        reasons.append("invalid_symbol_spec")
    if not (0 < spec.volume_min <= spec.volume_max):
        reasons.append("invalid_volume_range")

    for timeframe in required_timeframes:
        candles = snapshot.closed_candles(timeframe)
        if not candles:
            reasons.append(f"missing_closed_candles:{timeframe}")
            continue
        if any(c.high < max(c.open, c.close) or c.low > min(c.open, c.close) for c in candles):
            reasons.append(f"invalid_ohlc:{timeframe}")
        if any(a.open_time >= b.open_time for a, b in zip(candles, candles[1:])):
            reasons.append(f"unordered_candles:{timeframe}")

    return DataCheck(not reasons, tuple(reasons))
