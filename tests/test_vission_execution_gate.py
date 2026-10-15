#!/usr/bin/env python3
import unittest

from ai_trading_engine.learning import ProbabilityAssessment
from ai_trading_engine.models import Decision
from ai_trading_engine.scanner import _apply_vission_execution_decision


def assessment(execute: bool = False) -> ProbabilityAssessment:
    return ProbabilityAssessment(
        calibrated_probability=0.48,
        sample_size=0,
        historical_win_rate=None,
        expectancy_r=None,
        lower_probability_bound=None,
        execute=execute,
        reason="probability_gate_rejected:cold_start_ai_probability",
    )


class VissionExecutionGateTests(unittest.TestCase):
    def test_low_probability_approve_executes(self):
        result = _apply_vission_execution_decision(assessment(), Decision.APPROVE)
        self.assertTrue(result.execute)
        self.assertEqual(result.reason, "vission_approve_direct_execution")

    def test_abstain_never_executes(self):
        result = _apply_vission_execution_decision(assessment(True), Decision.ABSTAIN)
        self.assertFalse(result.execute)
        self.assertEqual(result.reason, "vission_abstain_blocked")

    def test_reject_never_executes(self):
        result = _apply_vission_execution_decision(assessment(True), Decision.REJECT)
        self.assertFalse(result.execute)
        self.assertEqual(result.reason, "vission_reject_blocked")


if __name__ == "__main__":
    unittest.main()
