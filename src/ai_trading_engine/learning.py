from __future__ import annotations

import json
import math
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Sequence

from .models import TradeCandidate, ValidationResult
from .risk import RiskCheck


class LearningStoreError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProbabilityAssessment:
    calibrated_probability: float
    sample_size: int
    historical_win_rate: float | None
    expectancy_r: float | None
    lower_probability_bound: float | None
    execute: bool
    reason: str


def _sql_text(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


class PostgresCliLearningStore:
    def __init__(self, container: str = "aitomate-postgres") -> None:
        self.command = [
            "docker",
            "exec",
            "-i",
            container,
            "psql",
            "-X",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            "aitomate",
            "-d",
            "aitomate_trading",
            "-At",
        ]

    def _run(self, sql: str) -> str:
        try:
            completed = subprocess.run(
                self.command,
                input=sql,
                check=False,
                capture_output=True,
                text=True,
                timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise LearningStoreError("learning database invocation failed") from exc
        if completed.returncode != 0:
            raise LearningStoreError("learning database rejected the operation")
        return completed.stdout.strip()

    def ingest_deals(self, deals: Sequence[dict[str, Any]], open_position_ids: Sequence[int]) -> None:
        statements: list[str] = []
        for deal in deals:
            try:
                occurred_at = datetime.fromtimestamp(float(deal["time_msc"]) / 1000.0, timezone.utc).isoformat()
                values = (
                    int(deal["ticket"]),
                    int(deal["position_id"]),
                    int(deal["entry"]),
                    int(deal["type"]),
                    float(deal["volume"]),
                    float(deal["price"]),
                    float(deal["profit"]),
                    float(deal["commission"]),
                    float(deal["swap"]),
                    str(deal.get("comment", "")),
                    occurred_at,
                )
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
            statements.append(
                "INSERT INTO broker_deals "
                "(deal_ticket,position_id,entry_type,direction_type,volume,price,profit,commission,swap,comment,occurred_at) VALUES "
                f"({values[0]},{values[1]},{values[2]},{values[3]},{values[4]},{values[5]},{values[6]},"
                f"{values[7]},{values[8]},{_sql_text(values[9])},{_sql_text(values[10])}::timestamptz) "
                "ON CONFLICT (deal_ticket) DO NOTHING;"
            )
        if statements:
            self._run("BEGIN;" + "".join(statements) + "COMMIT;")

        open_filter = ""
        if open_position_ids:
            ids = ",".join(str(int(item)) for item in open_position_ids)
            open_filter = f"AND entry_deal.position_id NOT IN ({ids})"
        self._run(
            "INSERT INTO learning_outcomes (setup_id,position_id,closed_at,pnl,r_multiple,won) "
            "SELECT prediction.setup_id, entry_deal.position_id, MAX(exit_deal.occurred_at), "
            "SUM(all_deal.profit + all_deal.commission + all_deal.swap) AS pnl, "
            "CASE WHEN prediction.estimated_loss > 0 THEN "
            "SUM(all_deal.profit + all_deal.commission + all_deal.swap) / prediction.estimated_loss ELSE 0 END, "
            "SUM(all_deal.profit + all_deal.commission + all_deal.swap) > 0 "
            "FROM trade_learning_predictions prediction "
            "JOIN broker_deals entry_deal ON entry_deal.entry_type = 0 "
            "AND entry_deal.comment = 'Vission-' || left(prediction.setup_id,16) "
            "JOIN broker_deals exit_deal ON exit_deal.position_id = entry_deal.position_id "
            "AND exit_deal.entry_type IN (1,3) "
            "JOIN broker_deals all_deal ON all_deal.position_id = entry_deal.position_id "
            f"WHERE prediction.gate_decision = 'EXECUTE' {open_filter} "
            "GROUP BY prediction.setup_id,entry_deal.position_id,prediction.estimated_loss "
            "ON CONFLICT (setup_id) DO UPDATE SET closed_at=EXCLUDED.closed_at,pnl=EXCLUDED.pnl,"
            "r_multiple=EXCLUDED.r_multiple,won=EXCLUDED.won,updated_at=now();"
        )

    def assess(self, candidate: TradeCandidate, raw_probability: float, net_reward_risk: float) -> ProbabilityAssessment:
        output = self._run(
            "SELECT count(*),coalesce(sum(CASE WHEN outcome.won THEN 1 ELSE 0 END),0),"
            "coalesce(avg(outcome.r_multiple),0) "
            "FROM trade_learning_predictions prediction "
            "JOIN learning_outcomes outcome USING (setup_id) "
            f"WHERE prediction.strategy_version={_sql_text(candidate.strategy_version)} "
            f"AND prediction.direction={_sql_text(candidate.direction.value)};"
        )
        try:
            count_text, wins_text, expectancy_text = output.split("|")
            sample_size = int(count_text)
            wins = int(wins_text)
            expectancy = float(expectancy_text)
        except (ValueError, AttributeError) as exc:
            raise LearningStoreError("invalid learning statistics") from exc

        historical_win_rate = None
        lower_bound = None
        calibrated = raw_probability
        reason = "cold_start_ai_probability"
        if sample_size >= 30:
            alpha = wins + 2.0
            beta = sample_size - wins + 2.0
            posterior_mean = alpha / (alpha + beta)
            posterior_variance = alpha * beta / (((alpha + beta) ** 2) * (alpha + beta + 1.0))
            lower_bound = max(0.0, posterior_mean - 1.645 * math.sqrt(posterior_variance))
            historical_win_rate = wins / sample_size
            calibrated = 0.35 * raw_probability + 0.65 * posterior_mean
            reason = "bayesian_direction_calibration"

        expected_r = calibrated * net_reward_risk - (1.0 - calibrated)
        execute = calibrated >= 0.70 and expected_r >= 0.20
        if sample_size >= 30:
            execute = execute and lower_bound is not None and lower_bound >= 0.45 and expectancy > 0
        gate_reason = reason if execute else f"probability_gate_rejected:{reason}"
        return ProbabilityAssessment(
            calibrated_probability=calibrated,
            sample_size=sample_size,
            historical_win_rate=historical_win_rate,
            expectancy_r=expectancy if sample_size else None,
            lower_probability_bound=lower_bound,
            execute=execute,
            reason=gate_reason,
        )

    def record_prediction(
        self,
        candidate: TradeCandidate,
        validation: ValidationResult,
        risk: RiskCheck,
        assessment: ProbabilityAssessment,
        features: dict[str, object],
    ) -> None:
        features_json = json.dumps(features, separators=(",", ":"), sort_keys=True)
        gate = "EXECUTE" if assessment.execute else "REJECT"
        self._run(
            "INSERT INTO trade_learning_predictions "
            "(setup_id,decided_at,strategy_version,direction,features,ai_probability,calibrated_probability,"
            "estimated_loss,net_reward_risk,gate_decision,gate_reason,ai_reason) VALUES ("
            f"{_sql_text(candidate.setup_id)},{_sql_text(validation.decided_at.isoformat())}::timestamptz,"
            f"{_sql_text(candidate.strategy_version)},{_sql_text(candidate.direction.value)},"
            f"{_sql_text(features_json)}::jsonb,{validation.probability},{assessment.calibrated_probability},"
            f"{risk.estimated_loss},{risk.net_reward_risk},{_sql_text(gate)},"
            f"{_sql_text(assessment.reason)},{_sql_text(validation.reason)}) "
            "ON CONFLICT (setup_id) DO NOTHING;"
        )
