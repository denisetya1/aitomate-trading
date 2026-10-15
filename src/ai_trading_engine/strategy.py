from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from hashlib import sha256
from statistics import median
from typing import Sequence

from .models import Candle, Direction, MarketSnapshot, TradeCandidate
from .risk import volume_for_risk


@dataclass(frozen=True)
class StrategyConfig:
    version: str = "price-action-adaptive-v2.0.0"
    h1_swing_left: int = 2
    h1_swing_right: int = 2
    range_lookback: int = 14
    pullback_m15_lookback: int = 4
    pullback_m5_lookback: int = 6
    sweep_lookback: int = 8
    stop_lookback: int = 5
    touch_tolerance_range: float = 0.25
    sweep_buffer_range: float = 0.02
    stop_buffer_range: float = 0.15
    min_body_range: float = 0.15
    min_close_location: float = 0.55
    min_range_width: float = 1.50
    pullback_reward_risk: float = 1.50
    range_min_reward_risk: float = 1.30
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
    breakout_level: float | None = None
    setup_type: str | None = None
    reference_level: float | None = None


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


def _structure_bias(candles: Sequence[Candle], config: StrategyConfig) -> Direction | None:
    swings = confirmed_swings(candles, config.h1_swing_left, config.h1_swing_right)
    highs = [swing.price for swing in swings if swing.kind == "HIGH"]
    lows = [swing.price for swing in swings if swing.kind == "LOW"]
    if len(highs) < 2 or len(lows) < 2:
        return None
    if highs[-1] > highs[-2] and lows[-1] > lows[-2]:
        return Direction.BUY
    if highs[-1] < highs[-2] and lows[-1] < lows[-2]:
        return Direction.SELL
    return None


def _m15_impulse(candles: Sequence[Candle], direction: Direction) -> bool:
    previous, latest = candles[-2], candles[-1]
    previous_body_high = max(previous.open, previous.close)
    previous_body_low = min(previous.open, previous.close)
    if direction is Direction.BUY:
        return latest.close > latest.open and latest.close > previous_body_high
    return latest.close < latest.open and latest.close < previous_body_low


def _typical_range(candles: Sequence[Candle], lookback: int) -> float:
    ranges = [candle.high - candle.low for candle in candles[-lookback:]]
    return median(ranges) if ranges else 0.0


def _has_pullback(candles: Sequence[Candle], direction: Direction, lookback: int) -> bool:
    recent = candles[-lookback:]
    if direction is Direction.BUY:
        return any(candle.close < candle.open for candle in recent)
    return any(candle.close > candle.open for candle in recent)


def _confirmation_quality(latest: Candle, direction: Direction, typical_range: float, config: StrategyConfig) -> bool:
    candle_range = latest.high - latest.low
    if candle_range <= 0:
        return False
    body = abs(latest.close - latest.open)
    close_location = (latest.close - latest.low) / candle_range
    if body < config.min_body_range * typical_range:
        return False
    if direction is Direction.BUY:
        return latest.close > latest.open and close_location >= config.min_close_location
    return latest.close < latest.open and close_location <= 1.0 - config.min_close_location


def _build_candidate(
    snapshot: MarketSnapshot,
    config: StrategyConfig,
    *,
    direction: Direction,
    latest: Candle,
    stop: float,
    target: float,
    setup_type: str,
    reference_level: float,
) -> StrategyResult:
    entry = snapshot.quote.ask if direction is Direction.BUY else snapshot.quote.bid
    risk_distance = entry - stop if direction is Direction.BUY else stop - entry
    reward_distance = target - entry if direction is Direction.BUY else entry - target
    if risk_distance <= 0 or reward_distance <= 0:
        return StrategyResult(None, "invalid_price_action_stop_or_target")

    risk_limited_volume = volume_for_risk(snapshot, entry, stop, config.risk_pct)
    volume = config.fixed_volume if config.fixed_volume is not None else risk_limited_volume
    if volume <= 0:
        return StrategyResult(None, "risk_budget_below_minimum_volume")
    if volume > risk_limited_volume:
        return StrategyResult(None, "fixed_volume_exceeds_risk_budget")

    strategy_version = f"{setup_type}-{config.version}"
    setup_seed = (
        f"{strategy_version}|{snapshot.symbol}|{direction.value}|"
        f"{latest.close_time.isoformat()}|{reference_level:.8f}"
    )
    setup_id = sha256(setup_seed.encode("utf-8")).hexdigest()[:20]
    candidate = TradeCandidate(
        setup_id=setup_id,
        strategy_version=strategy_version,
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
    return StrategyResult(
        candidate,
        f"{setup_type}_candidate_created",
        setup_type=setup_type,
        reference_level=reference_level,
    )


def _trend_pullback(
    snapshot: MarketSnapshot,
    m15: Sequence[Candle],
    m5: Sequence[Candle],
    direction: Direction,
    typical_range: float,
    config: StrategyConfig,
) -> StrategyResult:
    if not _has_pullback(m15, direction, config.pullback_m15_lookback):
        return StrategyResult(None, "m15_pullback_missing", setup_type="trend-pullback")

    latest = m5[-1]
    prior = m5[-config.pullback_m5_lookback - 1 : -1]
    if not _confirmation_quality(latest, direction, typical_range, config):
        return StrategyResult(None, "m5_pullback_confirmation_missing", setup_type="trend-pullback")

    tolerance = config.touch_tolerance_range * typical_range
    stop_buffer = config.stop_buffer_range * typical_range
    entry = snapshot.quote.ask if direction is Direction.BUY else snapshot.quote.bid
    if direction is Direction.BUY:
        level = min(candle.low for candle in prior)
        if latest.low > level + tolerance or latest.close <= level:
            return StrategyResult(None, "pullback_support_not_reclaimed", setup_type="trend-pullback", reference_level=level)
        structure_stop = min(candle.low for candle in m5[-config.stop_lookback :])
        stop = structure_stop - stop_buffer
        target = entry + config.pullback_reward_risk * (entry - stop)
    else:
        level = max(candle.high for candle in prior)
        if latest.high < level - tolerance or latest.close >= level:
            return StrategyResult(None, "pullback_resistance_not_reclaimed", setup_type="trend-pullback", reference_level=level)
        structure_stop = max(candle.high for candle in m5[-config.stop_lookback :])
        stop = structure_stop + stop_buffer
        target = entry - config.pullback_reward_risk * (stop - entry)

    return _build_candidate(
        snapshot,
        config,
        direction=direction,
        latest=latest,
        stop=stop,
        target=target,
        setup_type="trend-pullback",
        reference_level=level,
    )


def _range_sweep_reversal(
    snapshot: MarketSnapshot,
    m5: Sequence[Candle],
    typical_range: float,
    config: StrategyConfig,
) -> StrategyResult:
    latest = m5[-1]
    prior = m5[-config.sweep_lookback - 1 : -1]
    range_low = min(candle.low for candle in prior)
    range_high = max(candle.high for candle in prior)
    if range_high - range_low < config.min_range_width * typical_range:
        return StrategyResult(None, "range_too_narrow", setup_type="range-sweep-reversal")

    sweep_buffer = config.sweep_buffer_range * typical_range
    stop_buffer = config.stop_buffer_range * typical_range
    midpoint = (range_low + range_high) / 2.0
    buy_sweep = latest.low < range_low - sweep_buffer and latest.close > range_low
    sell_sweep = latest.high > range_high + sweep_buffer and latest.close < range_high

    if buy_sweep and _confirmation_quality(latest, Direction.BUY, typical_range, config):
        direction = Direction.BUY
        level = range_low
        entry = snapshot.quote.ask
        stop = latest.low - stop_buffer
        target = midpoint
    elif sell_sweep and _confirmation_quality(latest, Direction.SELL, typical_range, config):
        direction = Direction.SELL
        level = range_high
        entry = snapshot.quote.bid
        stop = latest.high + stop_buffer
        target = midpoint
    else:
        return StrategyResult(None, "range_liquidity_sweep_missing", setup_type="range-sweep-reversal")

    risk_distance = entry - stop if direction is Direction.BUY else stop - entry
    reward_distance = target - entry if direction is Direction.BUY else entry - target
    if risk_distance <= 0 or reward_distance / risk_distance < config.range_min_reward_risk:
        return StrategyResult(
            None,
            "range_reward_risk_too_low",
            setup_type="range-sweep-reversal",
            reference_level=level,
        )

    return _build_candidate(
        snapshot,
        config,
        direction=direction,
        latest=latest,
        stop=stop,
        target=target,
        setup_type="range-sweep-reversal",
        reference_level=level,
    )


def generate_candidate(snapshot: MarketSnapshot, config: StrategyConfig = StrategyConfig()) -> StrategyResult:
    h1 = snapshot.closed_candles("H1")
    m15 = snapshot.closed_candles("M15")
    m5 = snapshot.closed_candles("M5")
    minimum_h1 = config.h1_swing_left + config.h1_swing_right + 5
    minimum_m5 = max(
        config.range_lookback + 1,
        config.pullback_m5_lookback + 1,
        config.sweep_lookback + 1,
        config.stop_lookback,
    )
    if len(h1) < minimum_h1 or len(m15) < config.pullback_m15_lookback or len(m5) < minimum_m5:
        return StrategyResult(None, "insufficient_closed_candles")

    typical_range = _typical_range(m5[:-1], config.range_lookback)
    if typical_range <= 0:
        return StrategyResult(None, "invalid_price_range")

    direction = _structure_bias(h1, config)
    if direction is not None:
        return _trend_pullback(snapshot, m15, m5, direction, typical_range, config)
    return _range_sweep_reversal(snapshot, m5, typical_range, config)
