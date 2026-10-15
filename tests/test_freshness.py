import json
import os
import subprocess
import unittest
import tempfile
from pathlib import Path
from datetime import datetime, timezone
from dataclasses import replace
from datetime import timedelta
from unittest.mock import patch

from test_engine import NOW, candidate, snapshot
from ai_trading_engine.hermes import HermesCliValidator, HermesValidationError
from ai_trading_engine.models import Decision, ValidationResult
from ai_trading_engine.learning import ProbabilityAssessment
from ai_trading_engine.strategy import StrategyResult
from ai_trading_engine import scanner


class FreshnessTests(unittest.TestCase):
    def validator(self, age):
        return HermesCliValidator(clock=lambda: NOW + timedelta(seconds=age), runner=lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, json.dumps({'decision': 'APPROVE', 'setup_id': 'setup-1', 'probability': .7, 'reason': 'test'}), ''))

    def test_ai_context_25_seconds_is_accepted(self):
        self.validator(25).validate(candidate(), snapshot())

    def test_ai_context_over_30_seconds_is_rejected(self):
        with self.assertRaises(HermesValidationError):
            self.validator(31).validate(candidate(), snapshot())

    def test_prompt_separates_context_from_execution(self):
        prompt = HermesCliValidator._prompt(candidate(), snapshot())
        self.assertIn('30 seconds', prompt)
        self.assertIn('5 seconds', prompt)
        self.assertIn('candles', prompt)
        self.assertIn('candle_format', prompt)
        self.assertIn('Do not access or require any filesystem snapshot', prompt)
        self.assertIn('chart screenshot', prompt)
        self.assertIn('adaptive price-action', prompt)
        self.assertIn('trend pullback', prompt)
        self.assertIn('range liquidity-sweep reversal', prompt)
        self.assertIn('M5 rejection', prompt)
        self.assertIn('Do not require EMA', prompt)

    def test_validator_forces_low_reasoning_for_fast_decision(self):
        calls = []
        def runner(cmd, **kwargs):
            calls.append(cmd)
            return subprocess.CompletedProcess(
                cmd,
                0,
                json.dumps({'decision': 'REJECT', 'setup_id': 'setup-1', 'probability': .4, 'reason': 'test'}),
                '',
            )
        HermesCliValidator(runner=runner, clock=lambda: NOW).validate(candidate(), snapshot())
        self.assertIn('--reasoning', calls[0])
        self.assertEqual(calls[0][calls[0].index('--reasoning') + 1], 'low')
        self.assertIn('-m', calls[0])
        self.assertEqual(calls[0][calls[0].index('-m') + 1], 'gpt-6-luna')

    def test_timeout_error_names_the_actual_cause(self):
        def runner(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, kwargs['timeout'])

        with self.assertRaisesRegex(HermesValidationError, 'timed out after 17 seconds'):
            HermesCliValidator(
                timeout_seconds=17,
                runner=runner,
                clock=lambda: NOW,
            ).validate(candidate(), snapshot())

    def test_candidate_notifications_use_explicit_target(self):
        calls = []
        def runner(cmd, **kwargs):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, '', '')
        with patch.dict(os.environ, {'AITOMATE_TELEGRAM_TARGET': 'telegram:1088411814'}):
            self.assertTrue(scanner._send_telegram('vission', 'candidate', runner=runner))
        self.assertEqual(calls[0][3], 'telegram:1088411814')

    def test_symbol_exposure_counts_manual_position_and_pending_order(self):
        self.assertTrue(scanner._symbol_has_exposure({'positions': [{'symbol': 'EURUSDc'}]}, 'EURUSDc'))
        self.assertTrue(scanner._symbol_has_exposure({'orders': [{'symbol': 'EURUSDc'}]}, 'EURUSDc'))
        self.assertFalse(scanner._symbol_has_exposure({'positions': [{'symbol': 'GBPUSDc'}]}, 'EURUSDc'))

    def test_initial_scan_accepts_25_second_snapshot(self):
        now = datetime.now(timezone.utc)
        aged = replace(
            snapshot(),
            captured_at=now - timedelta(seconds=25),
            quote=replace(snapshot().quote, captured_at=now - timedelta(seconds=25)),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            market = root / 'market.json'
            market.write_text(json.dumps({'positions': [], 'deals': []}))
            with patch.object(scanner, 'load_execution_snapshot', return_value=aged), \
                 patch.object(scanner, 'notify_new_positions', return_value={}), \
                 patch.object(scanner, 'PostgresCliLearningStore'), \
                 patch.object(scanner, 'generate_candidate', return_value=StrategyResult(None, 'test_no_candidate')):
                result = scanner.scan_once(market, root / 'scanner.json', root / 'pending.json')
        self.assertEqual(result, {'status': 'no_candidate', 'reason': 'test_no_candidate'})

    def test_revalidation_requires_same_setup(self):
        from ai_trading_engine.revalidation import revalidate_approved
        new = candidate(setup_id='other')
        with patch('ai_trading_engine.revalidation.generate_candidate', return_value=StrategyResult(new, 'ok')):
            result = revalidate_approved(candidate(), snapshot(), now=NOW)
        self.assertIsNone(result.candidate)

    def test_revalidation_rejects_stale_quote(self):
        from ai_trading_engine.revalidation import revalidate_approved
        result = revalidate_approved(candidate(), snapshot(), now=NOW + timedelta(seconds=6))
        self.assertIsNone(result.candidate)
        self.assertIn('stale_quote', result.reason)

    def test_revalidation_preserves_stops_and_target(self):
        from ai_trading_engine.revalidation import revalidate_approved
        old = candidate(volume=.01)
        fresh = replace(snapshot(), snapshot_id='new')
        new = replace(old, snapshot_id='new', take_profit=2603.0)
        with patch('ai_trading_engine.revalidation.generate_candidate', return_value=StrategyResult(new, 'ok')):
            result = revalidate_approved(old, fresh, now=NOW)
        self.assertIsNotNone(result.candidate)
        self.assertEqual(result.candidate.take_profit, old.take_profit)
        self.assertEqual(result.candidate.stop_loss, old.stop_loss)
        self.assertEqual(result.candidate.snapshot_id, 'new')

    def test_revalidation_rejects_expired_setup(self):
        from ai_trading_engine.revalidation import revalidate_approved
        old = candidate(expires_at=NOW)
        result = revalidate_approved(old, snapshot(), now=NOW)
        self.assertIsNone(result.candidate)

    def test_vission_approved_candidate_cannot_open_when_fresh_setup_disappears(self):
        now = datetime.now(timezone.utc)
        initial = replace(snapshot(), captured_at=now, quote=replace(snapshot().quote, captured_at=now))
        old = candidate(created_at=now, expires_at=now + timedelta(minutes=5), volume=.01)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            market = root / 'market.json'
            market.write_text('{}')
            with patch.object(scanner, 'load_execution_snapshot', side_effect=[initial, initial]) as loader, \
                 patch.object(scanner, 'notify_new_positions', return_value={}), \
                 patch.object(scanner, 'notify_setup_once', return_value=True) as notify, \
                 patch.object(scanner, '_send_telegram', return_value=True) as send, \
                 patch.object(scanner, 'PostgresCliLearningStore') as learning, \
                 patch.object(scanner, 'generate_candidate', return_value=StrategyResult(old, 'ok')), \
                 patch.object(scanner.HermesCliValidator, 'validate', return_value=ValidationResult(Decision.APPROVE, old.setup_id, 'test', .7, now)), \
                 patch.object(scanner.subprocess, 'run') as execute:
                learning.return_value.assess.return_value = ProbabilityAssessment(.7, 50, None, None, None, True, 'test')
                result = scanner.scan_once(market, root / 'scanner.json', root / 'pending.json')
                self.assertEqual(result['status'], 'paper_rejected')
                self.assertEqual(result['reason'], 'insufficient_closed_candles')
                self.assertEqual(loader.call_count, 2)
                self.assertEqual(notify.call_args_list[0].args[2], 'candidate_setup_ids')
                learning.return_value.record_prediction.assert_called_once()
                assessment = learning.return_value.record_prediction.call_args.args[3]
                self.assertTrue(assessment.execute)
                self.assertEqual(assessment.reason, 'vission_approve_direct_execution')
                execute.assert_not_called()
                send.assert_called_once()
                self.assertIn('insufficient_closed_candles', send.call_args.args[1])
                self.assertFalse((root / 'pending.json').exists())

    def test_vission_approved_candidate_opens_paper_trade_without_real_execution(self):
        now = datetime.now(timezone.utc)
        initial = replace(snapshot(), captured_at=now, quote=replace(snapshot().quote, captured_at=now))
        old = candidate(created_at=now, expires_at=now + timedelta(minutes=5), volume=.01)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            market = root / 'market.json'
            market.write_text('{}')
            completed = subprocess.CompletedProcess([], 0, '{"ok":true,"status":"filled"}', '')
            with patch.object(scanner, 'load_execution_snapshot', side_effect=[initial, initial]), \
                 patch.object(scanner, 'notify_new_positions', return_value={}), \
                 patch.object(scanner, 'notify_setup_once', return_value=True), \
                 patch.object(scanner, 'PostgresCliLearningStore') as learning, \
                 patch.object(scanner, 'generate_candidate', return_value=StrategyResult(old, 'ok')), \
                 patch.object(scanner.HermesCliValidator, 'validate', return_value=ValidationResult(Decision.APPROVE, old.setup_id, 'test', .7, now)), \
                 patch.object(scanner, 'revalidate_approved', return_value=StrategyResult(old, 'ok')), \
                 patch.object(scanner.subprocess, 'run', return_value=completed) as execute:
                learning.return_value.assess.return_value = ProbabilityAssessment(.7, 50, None, None, None, True, 'test')
                result = scanner.scan_once(
                    market,
                    root / 'scanner.json',
                    root / 'pending.json',
                    hermes_executable='/does/not/exist',
                )
        self.assertEqual(result['status'], 'paper_opened')
        learning.return_value.record_prediction.assert_called_once()
        assessment = learning.return_value.record_prediction.call_args.args[3]
        self.assertTrue(assessment.execute)
        self.assertEqual(assessment.reason, 'vission_approve_direct_execution')
        features = learning.return_value.record_prediction.call_args.args[4]
        self.assertEqual(features['execution_mode'], 'paper_vission_validated')
        self.assertIn('candidate', features)
        self.assertIn('market_context', features)
        self.assertFalse(any('--confirm' in call.args[0] or 'REAL' in call.args[0] for call in execute.call_args_list))

    def test_vission_approved_candidate_submits_real_order_once(self):
        now = datetime.now(timezone.utc)
        initial = replace(snapshot(), captured_at=now, quote=replace(snapshot().quote, captured_at=now))
        old = candidate(created_at=now, expires_at=now + timedelta(minutes=5), volume=.01)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            market = root / 'market-XAUUSDc.json'
            market.write_text('{}')
            with patch.dict(os.environ, {'AITOMATE_EXECUTION_MODE': 'REAL'}), \
                 patch.object(scanner, 'load_execution_snapshot', side_effect=[initial, initial]), \
                 patch.object(scanner, 'notify_new_positions', return_value={}), \
                 patch.object(scanner, 'notify_setup_once', return_value=True), \
                 patch.object(scanner, '_send_telegram', return_value=True), \
                 patch.object(scanner, 'PostgresCliLearningStore') as learning, \
                 patch.object(scanner, 'generate_candidate', return_value=StrategyResult(old, 'ok')), \
                 patch.object(scanner.HermesCliValidator, 'validate', return_value=ValidationResult(Decision.APPROVE, old.setup_id, 'test', .7, now)), \
                 patch.object(scanner, 'revalidate_approved', return_value=StrategyResult(old, 'ok')), \
                 patch.object(scanner, '_execute_real', return_value={'ok': True, 'status': 'executed', 'deal_ticket': 123}) as execute:
                learning.return_value.assess.return_value = ProbabilityAssessment(.7, 50, None, None, None, True, 'test')
                result = scanner.scan_once(market, root / 'scanner-XAUUSDc.json', root / 'pending-XAUUSDc.json')
        self.assertEqual(result['status'], 'real_executed')
        self.assertEqual(result['deal_ticket'], 123)
        self.assertEqual(result['broker_status'], 'executed')
        execute.assert_called_once()

    def test_post_approval_latency_uses_fresh_execution_snapshot(self):
        class SequencedDateTime(datetime):
            values = iter(())

            @classmethod
            def now(cls, tz=None):
                return next(cls.values)

        base = SequencedDateTime(
            NOW.year, NOW.month, NOW.day, NOW.hour, NOW.minute, NOW.second,
            tzinfo=timezone.utc,
        )
        SequencedDateTime.values = iter((
            base,
            base,
            base + timedelta(seconds=29),
            base + timedelta(seconds=31),
        ))
        initial = replace(snapshot(), captured_at=base, quote=replace(snapshot().quote, captured_at=base))
        fresh_time = base + timedelta(seconds=31)
        fresh = replace(initial, snapshot_id='fresh', captured_at=fresh_time, quote=replace(initial.quote, captured_at=fresh_time))
        approved = candidate(created_at=base, expires_at=base + timedelta(minutes=5), volume=.01)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            market = root / 'market-XAUUSDc.json'
            market.write_text('{}')
            with patch.dict(os.environ, {'AITOMATE_EXECUTION_MODE': 'REAL'}), \
                 patch.object(scanner, 'datetime', SequencedDateTime), \
                 patch.object(scanner, 'load_execution_snapshot', side_effect=[initial, fresh]), \
                 patch.object(scanner, 'notify_new_positions', return_value={}), \
                 patch.object(scanner, 'notify_setup_once', return_value=True), \
                 patch.object(scanner, '_send_telegram', return_value=True), \
                 patch.object(scanner, 'PostgresCliLearningStore') as learning, \
                 patch.object(scanner, 'generate_candidate', return_value=StrategyResult(approved, 'ok')), \
                 patch.object(scanner.HermesCliValidator, 'validate', return_value=ValidationResult(Decision.APPROVE, approved.setup_id, 'test', .7, base + timedelta(seconds=29))), \
                 patch.object(scanner, 'revalidate_approved', return_value=StrategyResult(approved, 'ok')) as revalidate, \
                 patch.object(scanner, '_execute_real', return_value={'ok': True, 'status': 'executed', 'deal_ticket': 456}) as execute:
                learning.return_value.assess.return_value = ProbabilityAssessment(.7, 50, None, None, None, True, 'test')
                result = scanner.scan_once(market, root / 'scanner-XAUUSDc.json', root / 'pending-XAUUSDc.json')
        self.assertEqual(result['status'], 'real_executed')
        revalidate.assert_called_once()
        self.assertEqual(revalidate.call_args.args[1].snapshot_id, 'fresh')
        execute.assert_called_once()

    def test_cli_blocks_changed_setup_before_bridge_write(self):
        from ai_trading_engine import cli
        now = datetime.now(timezone.utc)
        fresh = replace(snapshot(), captured_at=now, quote=replace(snapshot().quote, captured_at=now))
        old = candidate(created_at=now, expires_at=now + timedelta(minutes=1), volume=.01)
        with patch.object(cli, 'load_execution_snapshot', return_value=fresh), \
             patch.object(cli, 'FileBridgeExecutor') as bridge, \
             patch('builtins.print'):
            result = cli.submit_real_order(Path('unused'), Path('unused'), old.direction, old.stop_loss, old.take_profit, 'REAL', approved_candidate=old)
        self.assertNotEqual(result, 0)
        bridge.assert_not_called()


if __name__ == '__main__':
    unittest.main()
