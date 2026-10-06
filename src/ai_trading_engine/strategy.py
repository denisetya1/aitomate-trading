from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from hashlib import sha256
from typing import Sequence

from .models import Candle, Direction, MarketSnapshot, TradeCandidate
from .risk import volume_for_risk


@dataclass(frozen=True)
class StrategyConfig:
    version: str = "trend-pullback-v1.0.0"
    h1_ema_fast: int = 50
    h1_ema_slow: int = 200
    m15_ema: int = 20
    atr_period: int = 14
    swing_left: int = 3
    swing_right: int = 3
    pullback_tolerance_atr: float = 0.25
    breakout_buffer_atr: float = 0.10
    retest_tolerance_atr: float = 0.15
    retest_max_bars: int = 3
    stop_buffer_atr: float = 0.20
    expiry_bars: int = 3
    risk_pct: float = 0.25
    fixed_volume: float | None = 0.01


@dataclass(frozen=True)
class Swing:
    index: int
    price: float
    kind: str


@dataclass(frozen=True)
class StrategyResult:
    candidate: TradeCandidate | None
    reason: str


def ema(values: Sequence[float], period: int) -> float:
    if period <= 0 or len(values) < period:
        raise ValueError("not enough values for EMA")
    seed = sum(values[:period]) / period
    multiplier = 2.0 / (period + 1.0)
    result = seed
    for value in values[period:]:
        result = (value - result) * multiplier + result
    return result


def atr(candles: Sequence[Candle], period: int = 14) -> float:
    if len(candles) < period + 1:
        raise ValueError("not enough candles for ATR")
    ranges: list[float] = []
    for previous, current in zip(candles, candles[1:]):
        ranges.append(
            max(
                current.high - current.low,
                abs(current.high - previous.close),
                abs(current.low - previous.close),
            )
        )
    return sum(ranges[-period:]) / period


def confirmed_swings(candles: Sequence[Candle], left: int = 3, right: int = 3) -> tuple[Swing, ...]:
    swings: list[Swing] = []
    for index in range(left, len(candles) - right):
        candle = candles[index]
        neighbors = candles[index - left : index] + candles[index + 1 : index + right + 1]
        if all(candle.high > other.high for other in neighbors):
            swings.append(Swing(index, candle.high, "HIGH"))
        if all(candle.low < other.low for other in neighbors):
            swings.append(Swing(index, candle.low, "LOW"))
    return tuple(swings)


def _trend(candles: Sequence[Candle], config: StrategyConfig) -> Direction | None:
    closes = [c.close for c in candles]
    fast = ema(closes, config.h1_ema_fast)
    slow = ema(closes, config.h1_ema_slow)
    swings = confirmed_swings(candles, config.swing_left, config.swing_right)
    highs = [s.price for s in swings if s.kind == "HIGH"]
    lows = [s.price for s in swings if s.kind == "LOW"]
    if len(highs) < 2 or len(lows) < 2:
        return None
    if fast > slow and highs[-1] > highs[-2] and lows[-1] > lows[-2]:
        return Direction.BUY
    if fast < slow and highs[-1] < highs[-2] and lows[-1] < lows[-2]:
        return Direction.SELL
    return None


def generate_candidate(snapshot: MarketSnapshot, config: StrategyConfig = StrategyConfig()) -> StrategyResult:
    h1 = snapshot.closed_candles("H1")
    m15 = snapshot.closed_candles("M15")
    m5 = snapshot.closed_candles("M5")
    minimum_h1 = config.h1_ema_slow + config.swing_right + 1
    if len(h1) < minimum_h1 or len(m15) < config.m15_ema + 1 or len(m5) < config.atr_period + 8:
        return StrategyResult(None, "insufficient_closed_candles")

    direction = _trend(h1, config)
    if direction is None:
        return StrategyResult(None, "h1_trend_not_confirmed")

    m15_atr = atr(m15, config.atr_period)
    m15_mean = ema([c.close for c in m15], config.m15_ema)
    pullback = m15[-1]
    tolerance = config.pullback_tolerance_atr * m15_atr
    if direction is Direction.BUY:
        pullback_ok = pullback.low <= m15_mean + tolerance and pullback.close >= m15_mean
    else:
        pullback_ok = pullback.high >= m15_mean - tolerance and pullback.close <= m15_mean
    if not pullback_ok:
        return StrategyResult(None, "m15_pullback_missing")

    m5_atr = atr(m5, config.atr_period)
    swings = confirmed_swings(m5, config.swing_left, config.swing_right)
    highs = [s for s in swings if s.kind == "HIGH"]
    lows = [s for s in swings if s.kind == "LOW"]
    if not highs or not lows:
        return StrategyResult(None, "m5_structure_missing")

    level = highs[-1].price if direction is Direction.BUY else lows[-1].price
    breakout_buffer = config.breakout_buffer_atr * m5_atr
    retest_tolerance = config.retest_tolerance_atr * m5_atr
    latest = m5[-1]
    search_start = max(0, len(m5) - config.retest_max_bars - 1)
    prior = m5[search_start:-1]
    if direction is Direction.BUY:
        breakout_ok = any(c.close > level + breakout_buffer for c in prior)
        retest_ok = latest.low <= level + retest_tolerance and latest.close > level
        stop = lows[-1].price - config.stop_buffer_atr * m5_atr
        targets = [s.price for s in confirmed_swings(h1, config.swing_left, config.swing_right) if s.kind == "HIGH" and s.price > snapshot.quote.ask]
        entry = snapshot.quote.ask
        target = min(targets) if targets else None
    else:
        breakout_ok = any(c.close < level - breakout_buffer for c in prior)
        retest_ok = latest.high >= level - retest_tolerance and latest.close < level
        stop = highs[-1].price + config.stop_buffer_atr * m5_atr
        targets = [s.price for s in confirmed_swings(h1, config.swing_left, config.swing_right) if s.kind == "LOW" and s.price < snapshot.quote.bid]
        entry = snapshot.quote.bid
        target = max(targets) if targets else None

    if not breakout_ok:
        return StrategyResult(None, "m5_breakout_missing")
    if not retest_ok:
        return StrategyResult(None, "m5_retest_missing")
    if target is None:
        return StrategyResult(None, "h1_target_missing")

    risk_limited_volume = volume_for_risk(snapshot, entry, stop, config.risk_pct)
    volume = config.fixed_volume if config.fixed_volume is not None else risk_limited_volume
    if volume <= 0:
        return StrategyResult(None, "risk_budget_below_minimum_volume")
    if volume > risk_limited_volume:
        return StrategyResult(None, "fixed_volume_exceeds_risk_budget")

    setup_seed = f"{config.version}|{snapshot.snapshot_id}|{direction.value}|{level:.8f}"
    setup_id = sha256(setup_seed.encode("utf-8")).hexdigest()[:20]
    candidate = TradeCandidate(
        setup_id=setup_id,
        strategy_version=config.version,
        snapshot_id=snapshot.snapshot_id,
        created_at=snapshot.captured_at,
        expires_at=latest.close_time + timedelta(minutes=5 * config.expiry_bars),
        symbol=snapshot.symbol,
        direction=direction,
        entry=entry,
        stop_loss=stop,
        take_profit=target,
        volume=volume,
    )
    return StrategyResult(candidate, "candidate_created")
