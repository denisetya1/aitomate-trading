from __future__ import annotations

import subprocess
import unittest
from datetime import datetime, timedelta, timezone

from ai_trading_engine.execution import LockedRealExecutor, RealExecutionLocked, ReplayExecutor
from ai_trading_engine.hermes import HermesCliValidator, HermesValidationError, parse_validation
from ai_trading_engine.models import (
    AccountState,
    Candle,
    Decision,
    Direction,
    MarketSnapshot,
    Quote,
    SymbolSpec,
    TradeCandidate,
)
from ai_trading_engine.pipeline import evaluate_candidate
from ai_trading_engine.risk import RiskPolicy, check_candidate, volume_for_risk
from ai_trading_engine.strategy import StrategyConfig, confirmed_swings, generate_candidate
from ai_trading_engine.validation import validate_snapshot


NOW = datetime(2026, 10, 7, 6, 0, tzinfo=timezone.utc)


def snapshot(**changes: object) -> MarketSnapshot:
    closed = Candle(NOW - timedelta(minutes=10), NOW - timedelta(minutes=5), 2600, 2602, 2599, 2601)
    active = Candle(NOW - timedelta(minutes=5), NOW + timedelta(seconds=1), 2601, 2603, 2600, 2602)
    values = {
        "snapshot_id": "snap-1",
        "captured_at": NOW,
        "symbol": "XAUUSDc",
        "quote": Quote(NOW, 2600.000, 2600.050),
        "symbol_spec": SymbolSpec("XAUUSDc", 3, 0.001, 0.01, 1.0, 100.0, 0.01, 100.0, 0.01, 10, 0),
        "account": AccountState(10_000, 10_000, 0),
        "candles": {"M5": [closed, active], "M15": [closed], "H1": [closed]},
        "open_positions": 0,
        "pending_orders": 0,
    }
    values.update(changes)
    return MarketSnapshot(**values)  # type: ignore[arg-type]


def candidate(**changes: object) -> TradeCandidate:
    values = {
        "setup_id": "setup-1",
        "strategy_version": "trend-pullback-v1.0.0",
        "snapshot_id": "snap-1",
        "created_at": NOW,
        "expires_at": NOW + timedelta(minutes=15),
        "symbol": "XAUUSDc",
        "direction": Direction.BUY,
        "entry": 2600.05,
        "stop_loss": 2599.05,
        "take_profit": 2602.05,
        "volume": 0.2,
    }
    values.update(changes)
    return TradeCandidate(**values)  # type: ignore[arg-type]


class DataValidationTests(unittest.TestCase):
    def test_active_candle_is_excluded(self) -> None:
        self.assertEqual(len(snapshot().closed_candles("M5")), 1)

    def test_stale_quote_is_rejected(self) -> None:
        stale = snapshot(quote=Quote(NOW - timedelta(seconds=6), 2600, 2600.05))
        result = validate_snapshot(stale, now=NOW)
        self.assertFalse(result.ok)
        self.assertIn("stale_quote", result.reasons)


class StrategyTests(unittest.TestCase):
    def test_swing_is_not_confirmed_without_right_bars(self) -> None:
        candles = [
            Candle(NOW + timedelta(minutes=i), NOW + timedelta(minutes=i + 1), 1, high, 0, 1)
            for i, high in enumerate([1, 2, 5, 2, 1])
        ]
        self.assertEqual(confirmed_swings(candles[:4], left=2, right=2), ())
        swings = confirmed_swings(candles, left=2, right=2)
        self.assertEqual([(s.index, s.kind) for s in swings], [(2, "HIGH")])

    def test_strategy_refuses_short_history(self) -> None:
        result = generate_candidate(snapshot(), StrategyConfig())
        self.assertIsNone(result.candidate)
        self.assertEqual(result.reason, "insufficient_closed_candles")


class RiskTests(unittest.TestCase):
    def test_wrong_stop_side_is_rejected(self) -> None:
        result = check_candidate(candidate(stop_loss=2601), snapshot(), now=NOW)
        self.assertIn("stop_wrong_side", result.reasons)

    def test_daily_loss_limit_is_enforced(self) -> None:
        state = AccountState(9_900, 10_000, -100)
        result = check_candidate(candidate(), snapshot(account=state), now=NOW)
        self.assertIn("daily_loss_limit", result.reasons)

    def test_one_position_limit_is_enforced(self) -> None:
        result = check_candidate(candidate(), snapshot(open_positions=1), now=NOW)
        self.assertIn("position_limit", result.reasons)

    def test_volume_is_floored_to_broker_step(self) -> None:
        value = volume_for_risk(snapshot(), 2600, 2599, 0.25)
        self.assertEqual(value, 0.25)


class HermesTests(unittest.TestCase):
    def test_valid_response(self) -> None:
        result = parse_validation(
            '{"decision":"APPROVE","setup_id":"setup-1","reason":"structure aligned"}',
            candidate(),
            NOW,
        )
        self.assertEqual(result.decision, Decision.APPROVE)

    def test_mismatched_setup_is_rejected(self) -> None:
        with self.assertRaises(HermesValidationError):
            parse_validation('{"decision":"APPROVE","setup_id":"other","reason":"ok"}', candidate(), NOW)

    def test_parameter_change_is_rejected(self) -> None:
        with self.assertRaises(HermesValidationError):
            parse_validation(
                '{"decision":"APPROVE","setup_id":"setup-1","reason":"ok","volume":1}', candidate(), NOW
            )

    def test_cli_uses_argv_without_shell(self) -> None:
        calls: list[tuple[list[str], dict[str, object]]] = []

        def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(
                command, 0, '{"decision":"REJECT","setup_id":"setup-1","reason":"conflict"}', ""
            )

        result = HermesCliValidator(runner=runner, clock=lambda: NOW).validate(candidate(), snapshot())
        self.assertEqual(result.decision, Decision.REJECT)
        self.assertNotIn("shell", calls[0][1])
        self.assertIsInstance(calls[0][0], list)


class ExecutionTests(unittest.TestCase):
    def test_replay_executor_is_idempotent(self) -> None:
        executor = ReplayExecutor()
        first = executor.submit("req-1", candidate())
        second = executor.submit("req-1", candidate())
        self.assertEqual(first, second)

    def test_real_execution_is_locked(self) -> None:
        with self.assertRaises(RealExecutionLocked):
            LockedRealExecutor().submit("req-1", candidate())

    def test_pipeline_records_approved_candidate(self) -> None:
        result = evaluate_candidate(candidate(), snapshot(), ReplayExecutor(), now=NOW)
        self.assertEqual(result.status, "RECORDED")


if __name__ == "__main__":
    unittest.main()
