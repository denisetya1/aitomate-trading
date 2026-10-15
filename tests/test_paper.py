import tempfile
import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from test_engine import NOW, candidate, snapshot
from ai_trading_engine.paper import has_open_paper_trade, open_paper_trade, settle_paper_trades
from ai_trading_engine.risk import RiskCheck


class PaperTradingTests(unittest.TestCase):
    def test_buy_hits_target_and_closes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "paper.json"
            trade = candidate(entry=2600.0, stop_loss=2599.0, take_profit=2602.0)
            risk = RiskCheck(True, (), 10.0, 2.0)
            self.assertTrue(open_paper_trade(path, trade, risk, opened_at=NOW))
            self.assertTrue(has_open_paper_trade(path))
            market = replace(snapshot(), quote=replace(snapshot().quote, bid=2602.1, ask=2602.2))
            settled = settle_paper_trades(path, market)
            self.assertEqual(settled[0]["exit_reason"], "TP")
            self.assertEqual(settled[0]["r_multiple"], 2.0)
            self.assertFalse(has_open_paper_trade(path))

    def test_failed_breakout_exits_before_full_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "paper.json"
            trade = candidate(entry=2600.0, stop_loss=2590.0, take_profit=2620.0)
            risk = RiskCheck(True, (), 10.0, 2.0)
            self.assertTrue(
                open_paper_trade(path, trade, risk, opened_at=NOW, breakout_level=2599.5)
            )
            invalidation_candle = replace(
                snapshot().candles["M5"][0],
                open_time=NOW,
                close_time=NOW + timedelta(minutes=5),
                close=2599.4,
            )
            market = replace(
                snapshot(),
                captured_at=NOW + timedelta(minutes=5, seconds=1),
                quote=replace(snapshot().quote, captured_at=NOW + timedelta(minutes=5, seconds=1), bid=2599.3, ask=2599.5),
                candles={"M5": [invalidation_candle]},
            )
            settled = settle_paper_trades(path, market)
            self.assertEqual(settled[0]["exit_reason"], "BREAKOUT_INVALIDATED")
            self.assertGreater(settled[0]["r_multiple"], -1.0)

    def test_scalp_has_time_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "paper.json"
            trade = candidate(entry=2600.0, stop_loss=2590.0, take_profit=2620.0)
            risk = RiskCheck(True, (), 10.0, 2.0)
            self.assertTrue(open_paper_trade(path, trade, risk, opened_at=NOW, max_holding_minutes=30))
            market = replace(
                snapshot(),
                captured_at=NOW + timedelta(minutes=31),
                quote=replace(snapshot().quote, captured_at=NOW + timedelta(minutes=31), bid=2600.2, ask=2600.4),
            )
            settled = settle_paper_trades(path, market)
            self.assertEqual(settled[0]["exit_reason"], "TIME_STOP")

    def test_only_one_paper_position_can_be_open(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "paper.json"
            risk = RiskCheck(True, (), 10.0, 2.0)
            self.assertTrue(open_paper_trade(path, candidate(), risk, opened_at=NOW))
            self.assertFalse(open_paper_trade(path, candidate(setup_id="other"), risk, opened_at=NOW))


if __name__ == "__main__":
    unittest.main()
