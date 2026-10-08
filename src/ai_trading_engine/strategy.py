from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from hashlib import sha256
from typing import Sequence

from .models import Candle, Direction, MarketSnapshot, TradeCandidate
from .risk import volume_for_risk


@dataclass(frozen=True)
class StrategyConfig:
    version: str = "xau-scalping-v1.0.0"
    m15_ema_fast: int = 20
    m15_ema_slow: int = 50
    m5_ema_fast: int = 9
    m5_ema_slow: int = 21
    atr_period: int = 14
    breakout_lookback: int = 5
    breakout_buffer_atr: float = 0.02
    stop_lookback: int = 5
    stop_buffer_atr: float = 0.15
    max_chase_atr: float = 0.50
    reward_risk: float = 2.20
    expiry_bars: int = 2
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


def _m15_bias(candles: Sequence[Candle], config: StrategyConfig) -> Direction | None:
    closes = [candle.close for candle in candles]
    fast = ema(closes, config.m15_ema_fast)
    slow = ema(closes, config.m15_ema_slow)
    latest = candles[-1]
    if fast > slow and latest.close > fast:
        return Direction.BUY
    if fast < slow and latest.close < fast:
        return Direction.SELL
    return None


def generate_candidate(snapshot: MarketSnapshot, config: StrategyConfig = StrategyConfig()) -> StrategyResult:
    m15 = snapshot.closed_candles("M15")
    m5 = snapshot.closed_candles("M5")
    minimum_m15 = config.m15_ema_slow
    minimum_m5 = max(config.m5_ema_slow, config.atr_period + 1, config.breakout_lookback + 1)
    if len(m15) < minimum_m15 or len(m5) < minimum_m5:
        return StrategyResult(None, "insufficient_closed_candles")

    direction = _m15_bias(m15, config)
    if direction is None:
        return StrategyResult(None, "m15_bias_not_confirmed")

    m5_closes = [candle.close for candle in m5]
    m5_fast = ema(m5_closes, config.m5_ema_fast)
    m5_slow = ema(m5_closes, config.m5_ema_slow)
    if (direction is Direction.BUY and m5_fast <= m5_slow) or (
        direction is Direction.SELL and m5_fast >= m5_slow
    ):
        return StrategyResult(None, "m5_momentum_not_confirmed")

    m5_atr = atr(m5, config.atr_period)
    latest = m5[-1]
    prior = m5[-config.breakout_lookback - 1 : -1]
    buffer = config.breakout_buffer_atr * m5_atr
    if direction is Direction.BUY:
        level = max(candle.high for candle in prior)
        breakout_ok = latest.close > level + buffer and latest.close > latest.open
        entry = snapshot.quote.ask
        entry_ok = level < entry <= latest.close + config.max_chase_atr * m5_atr
        structure_stop = min(candle.low for candle in m5[-config.stop_lookback :])
        stop = structure_stop - config.stop_buffer_atr * m5_atr
        risk_distance = entry - stop
        target = entry + config.reward_risk * risk_distance
    else:
        level = min(candle.low for candle in prior)
        breakout_ok = latest.close < level - buffer and latest.close < latest.open
        entry = snapshot.quote.bid
        entry_ok = latest.close - config.max_chase_atr * m5_atr <= entry < level
        structure_stop = max(candle.high for candle in m5[-config.stop_lookback :])
        stop = structure_stop + config.stop_buffer_atr * m5_atr
        risk_distance = stop - entry
        target = entry - config.reward_risk * risk_distance

    if not breakout_ok:
        return StrategyResult(None, "m5_breakout_missing")
    if not entry_ok:
        return StrategyResult(None, "entry_outside_scalp_range")
    if risk_distance <= 0:
        return StrategyResult(None, "invalid_scalp_stop")

    risk_limited_volume = volume_for_risk(snapshot, entry, stop, config.risk_pct)
    volume = config.fixed_volume if config.fixed_volume is not None else risk_limited_volume
    if volume <= 0:
        return StrategyResult(None, "risk_budget_below_minimum_volume")
    if volume > risk_limited_volume:
        return StrategyResult(None, "fixed_volume_exceeds_risk_budget")

    setup_seed = (
        f"{config.version}|{snapshot.symbol}|{direction.value}|"
        f"{latest.close_time.isoformat()}|{level:.8f}"
    )
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
    return StrategyResult(candidate, "scalping_candidate_created")
