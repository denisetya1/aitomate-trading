from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .execution import ExecutionReceipt, Executor
from .hermes import HermesValidationError
from .models import Decision, MarketSnapshot, TradeCandidate, ValidationResult
from .risk import RiskCheck, RiskPolicy, check_candidate
from .validation import DataCheck, validate_snapshot


@dataclass(frozen=True)
class PipelineResult:
    status: str
    data_check: DataCheck
    risk_check: RiskCheck | None = None
    ai_validation: ValidationResult | None = None
    receipt: ExecutionReceipt | None = None


def evaluate_candidate(
    candidate: TradeCandidate,
    snapshot: MarketSnapshot,
    executor: Executor,
    *,
    validator: object | None = None,
    risk_policy: RiskPolicy = RiskPolicy(),
    now: datetime | None = None,
) -> PipelineResult:
    now = now or datetime.now(timezone.utc)
    data = validate_snapshot(snapshot, now=now, max_quote_age=timedelta(seconds=5))
    if not data.ok:
        return PipelineResult("DATA_REJECTED", data)

    risk = check_candidate(candidate, snapshot, risk_policy, now=now)
    if not risk.ok:
        return PipelineResult("RISK_REJECTED", data, risk)

    ai_result = None
    if validator is not None:
        try:
            ai_result = validator.validate(candidate, snapshot)  # type: ignore[attr-defined]
        except HermesValidationError:
            return PipelineResult("AI_ABSTAINED", data, risk)
        if ai_result.decision is not Decision.APPROVE:
            return PipelineResult(f"AI_{ai_result.decision.value}", data, risk, ai_result)

    second_risk = check_candidate(candidate, snapshot, risk_policy, now=now)
    if not second_risk.ok:
        return PipelineResult("FINAL_RISK_REJECTED", data, second_risk, ai_result)
    request_id = f"{candidate.setup_id}:entry"
    receipt = executor.submit(request_id, candidate)
    return PipelineResult("RECORDED" if receipt.accepted else "EXECUTION_REJECTED", data, second_risk, ai_result, receipt)
