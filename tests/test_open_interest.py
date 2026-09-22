import unittest
from unittest.mock import MagicMock, patch

from data.binance_client import BinanceAPIError
from data.open_interest import get_open_interest_change


class OpenInterestTests(unittest.TestCase):
    def test_get_open_interest_change_handles_missing_history_gracefully(self):
        client = MagicMock()
        client.get_open_interest.return_value = {"openInterest": 100.0}
        client.get_open_interest_history.side_effect = BinanceAPIError("history endpoint missing")

        with patch("data.open_interest.db.save_open_interest_snapshot") as save_mock:
            result = get_open_interest_change(client, "BTCUSDT")

        self.assertEqual(100.0, result["current_oi"])
        self.assertIsNone(result["oi_change_pct"])
        self.assertIsNone(result["oi_15m_change_pct"])
        self.assertIsNone(result["oi_30m_change_pct"])
        self.assertIsNone(result["oi_1h_change_pct"])
        self.assertFalse(result["oi_increased"])
        self.assertFalse(result["oi_15m_increased"])
        self.assertFalse(result["oi_30m_increased"])
        self.assertFalse(result["oi_1h_increased"])
        save_mock.assert_called_once_with("BTCUSDT", 100.0)


if __name__ == "__main__":
    unittest.main()
