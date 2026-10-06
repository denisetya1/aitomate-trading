from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_FLOOR

from .models import Direction, MarketSnapshot, TradeCandidate


@dataclass(frozen=True)
class RiskPolicy:
    risk_per_trade_pct: float = 0.25
    daily_loss_limit_pct: float = 1.0
    max_positions: int = 1
    max_spread_points: float = 80.0
    min_net_reward_risk: float = 1.5


@dataclass(frozen=True)
class RiskCheck:
    ok: bool
    reasons: tuple[str, ...]
    estimated_loss: float
    net_reward_risk: float


def floor_volume(raw_volume: float, minimum: float, maximum: float, step: float) -> float:
    if raw_volume < minimum:
        return 0.0
    units = (Decimal(str(min(raw_volume, maximum))) / Decimal(str(step))).to_integral_value(rounding=ROUND_FLOOR)
    return float(units * Decimal(str(step)))


def volume_for_risk(snapshot: MarketSnapshot, entry: float, stop_loss: float, risk_pct: float) -> float:
    spec = snapshot.symbol_spec
    distance = abs(entry - stop_loss)
    if distance <= 0 or spec.tick_size <= 0 or spec.tick_value <= 0:
        return 0.0
    risk_budget = snapshot.account.equity * risk_pct / 100.0
    loss_per_lot = distance / spec.tick_size * spec.tick_value
    return floor_volume(risk_budget / loss_per_lot, spec.volume_min, spec.volume_max, spec.volume_step)


def check_candidate(
    candidate: TradeCandidate,
    snapshot: MarketSnapshot,
    policy: RiskPolicy = RiskPolicy(),
    *,
    now: datetime | None = None,
) -> RiskCheck:
    now = now or datetime.now(timezone.utc)
    reasons: list[str] = []
    spec = snapshot.symbol_spec
    spread = snapshot.quote.ask - snapshot.quote.bid
    spread_points = spread / spec.point

    if candidate.symbol != snapshot.symbol:
        reasons.append("symbol_mismatch")
    if candidate.snapshot_id != snapshot.snapshot_id:
        reasons.append("snapshot_mismatch")
    if now >= candidate.expires_at:
        reasons.append("candidate_expired")
    if snapshot.open_positions + snapshot.pending_orders >= policy.max_positions:
        reasons.append("position_limit")
    daily_floor = -(snapshot.account.day_start_equity * policy.daily_loss_limit_pct / 100.0)
    if snapshot.account.realized_pnl_today <= daily_floor:
        reasons.append("daily_loss_limit")
    if spread_points > policy.max_spread_points:
        reasons.append("spread_limit")
    if not (spec.volume_min <= candidate.volume <= spec.volume_max):
        reasons.append("volume_range")
    step_units = candidate.volume / spec.volume_step
    if abs(step_units - round(step_units)) > 1e-8:
        reasons.append("volume_step")

    if candidate.direction is Direction.BUY:
        risk_distance = candidate.entry - candidate.stop_loss
        reward_distance = candidate.take_profit - candidate.entry
    else:
        risk_distance = candidate.stop_loss - candidate.entry
        reward_distance = candidate.entry - candidate.take_profit
    if risk_distance <= 0:
        reasons.append("stop_wrong_side")
    if reward_distance <= 0:
        reasons.append("target_wrong_side")

    estimated_loss = 0.0
    net_rr = 0.0
    if risk_distance > 0:
        estimated_loss = risk_distance / spec.tick_size * spec.tick_value * candidate.volume
        net_reward = max(0.0, reward_distance - spread)
        net_risk = risk_distance + spread
        net_rr = net_reward / net_risk
        risk_budget = snapshot.account.equity * policy.risk_per_trade_pct / 100.0
        if estimated_loss > risk_budget + 1e-8:
            reasons.append("trade_risk_limit")
        if net_rr < policy.min_net_reward_risk:
            reasons.append("reward_risk_limit")

    min_stop_distance = spec.stops_level_points * spec.point
    if risk_distance > 0 and risk_distance < min_stop_distance:
        reasons.append("broker_stop_level")

    return RiskCheck(not reasons, tuple(reasons), estimated_loss, net_rr)
