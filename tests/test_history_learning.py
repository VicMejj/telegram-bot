import unittest

from strategy.history_learning import build_history_adjustment


class HistoryLearningTests(unittest.TestCase):
    def test_build_history_adjustment_returns_bonus_for_compatible_history(self):
        history_rows = [
            {"setup_type": "MA Trend + OI Rising + Volume Surge + Healthy Funding + Structure Intact", "score": 82, "success": True},
            {"setup_type": "MA Trend + OI Rising + Volume Surge + Healthy Funding + Structure Intact", "score": 78, "success": True},
            {"setup_type": "MA Trend + OI Rising + Volume Surge + Healthy Funding + Structure Intact", "score": 74, "success": False},
        ]

        adjustment = build_history_adjustment(
            current_score=80,
            current_setup_type="MA Trend + OI Rising + Volume Surge + Healthy Funding + Structure Intact",
            history_rows=history_rows,
            current_symbol="BTCUSDT",
            current_timeframe="1h",
        )

        self.assertGreater(adjustment["bonus_points"], 0)
        self.assertIn("history", adjustment["reason"].lower())

    def test_build_history_adjustment_returns_zero_when_history_is_sparse(self):
        adjustment = build_history_adjustment(
            current_score=80,
            current_setup_type="MA Trend + OI Rising + Volume Surge + Healthy Funding + Structure Intact",
            history_rows=[{"setup_type": "Other Setup", "score": 70, "success": True}],
            current_symbol="BTCUSDT",
            current_timeframe="1h",
        )

        self.assertEqual(adjustment["bonus_points"], 0)


if __name__ == "__main__":
    unittest.main()
