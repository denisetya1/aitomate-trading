from dataclasses import replace
from datetime import datetime, timedelta

from .models import MarketSnapshot, TradeCandidate
from .risk import RiskPolicy, check_candidate
from .strategy import StrategyConfig, StrategyResult, generate_candidate
from .validation import validate_snapshot


def revalidate_approved(approved: TradeCandidate, snapshot: MarketSnapshot, *, now: datetime, policy: RiskPolicy | None = None) -> StrategyResult:
    data = validate_snapshot(snapshot, now=now, max_quote_age=timedelta(seconds=5))
    if not data.ok:
        return StrategyResult(None, ','.join(data.reasons))
    if now >= approved.expires_at:
        return StrategyResult(None, 'candidate_expired')
    result = generate_candidate(snapshot, StrategyConfig(fixed_volume=0.01))
    current = result.candidate
    if current is None:
        return result
    if (current.setup_id, current.symbol, current.direction, current.volume, current.stop_loss) != (
        approved.setup_id, approved.symbol, approved.direction, approved.volume, approved.stop_loss
    ):
        return StrategyResult(None, 'approved_setup_changed')
    # Re-price risk against the new quote, never widen stops or move approved targets.
    candidate = replace(approved, snapshot_id=snapshot.snapshot_id, entry=current.entry)
    risk = check_candidate(candidate, snapshot, policy or RiskPolicy(max_positions=1, max_spread_points=400.0, min_net_reward_risk=1.5), now=now)
    if not risk.ok:
        return StrategyResult(None, ','.join(risk.reasons))
    return StrategyResult(candidate, 'approved_setup_revalidated')
