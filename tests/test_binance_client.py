import unittest
from unittest.mock import patch

from data.binance_client import BinanceClient


class BinanceClientTests(unittest.TestCase):
    def test_open_interest_history_uses_futures_data_endpoint(self):
        client = BinanceClient()

        with patch.object(client, "_get", return_value=[]) as get_mock:
            result = client.get_open_interest_history("BTCUSDT", "15m", limit=2)

        self.assertEqual([], result)
        get_mock.assert_called_once_with(
            "/futures/data/openInterestHist",
            params={"symbol": "BTCUSDT", "period": "15m", "limit": 2},
        )


if __name__ == "__main__":
    unittest.main()
