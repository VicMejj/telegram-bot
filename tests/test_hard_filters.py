import unittest
from unittest.mock import patch

import pandas as pd

from strategy.ma_oi_funding_strategy import evaluate_symbol
from strategy.scoring import passes_hard_filters


class HardFilterTests(unittest.TestCase):
    def test_accepts_strong_bullish_setup(self):
        indicators = {
            "current_price": 110.0,
            "ma7": 100.0,
            "ma25": 95.0,
            "ma99": 90.0,
            "volume_ratio": 2.5,
        }
        oi_info = {
            "oi_15m_change_pct": 2.5,
            "oi_30m_change_pct": 3.5,
            "oi_1h_change_pct": 6.0,
        }

        passed, reasons = passes_hard_filters(indicators, 0.0002, oi_info)

        self.assertTrue(passed)
        self.assertEqual([], reasons)

    def test_rejects_when_any_required_filter_fails(self):
        indicators = {
            "current_price": 100.0,
            "ma7": 95.0,
            "ma25": 98.0,
            "ma99": 90.0,
            "volume_ratio": 1.2,
        }
        oi_info = {
            "oi_15m_change_pct": 1.0,
            "oi_30m_change_pct": 2.0,
            "oi_1h_change_pct": 3.0,
        }

        passed, reasons = passes_hard_filters(indicators, 0.0002, oi_info)

        self.assertFalse(passed)
        self.assertTrue(any("MA7" in reason for reason in reasons))
        self.assertTrue(any("Volume" in reason for reason in reasons))
        self.assertTrue(any("OI" in reason for reason in reasons))

    @patch("strategy.ma_oi_funding_strategy.score_setup", side_effect=AssertionError("score_setup should not run when filters fail"))
    @patch("strategy.ma_oi_funding_strategy.passes_hard_filters", return_value=(False, ["Volume below threshold"]))
    @patch("strategy.ma_oi_funding_strategy.get_open_interest_change", return_value={
        "current_oi": 100.0,
        "oi_change_pct": 0.5,
        "oi_15m_change_pct": 1.0,
        "oi_30m_change_pct": 2.0,
        "oi_1h_change_pct": 3.0,
        "oi_increased": True,
    })
    @patch("strategy.ma_oi_funding_strategy.get_funding_rate", return_value=0.0001)
    @patch("strategy.ma_oi_funding_strategy.build_indicator_snapshot", return_value={
        "current_price": 110.0,
        "ma7": 105.0,
        "ma25": 100.0,
        "ma99": 95.0,
        "holding_ma7": True,
        "holding_ma25": True,
        "holding_ma99": True,
        "volume_ratio": 1.2,
        "is_overextended": False,
        "extension_pct": 0.0,
    })
    @patch("strategy.ma_oi_funding_strategy.get_closed_candles_df", return_value=pd.DataFrame([{"close": 100.0}] * 10))
    def test_evaluate_symbol_skips_scoring_when_hard_filters_fail(self, *_mocks):
        evaluation = evaluate_symbol(client=object(), symbol="BTCUSDT")

        self.assertIsNotNone(evaluation)
        self.assertFalse(evaluation["passes_hard_filters"])
        self.assertEqual(0, evaluation["score"])
        self.assertEqual(["Volume below threshold"], evaluation["hard_filter_reasons"])
        self.assertEqual(["Volume below threshold"], evaluation["reasons"])
