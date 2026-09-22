import unittest
from unittest.mock import patch, Mock

from data.binance_client import BinanceAPIError
import main


class MainStartupTests(unittest.TestCase):
    def test_main_continues_when_initial_binance_lookup_fails(self):
        with patch("main.logging_setup.configure_logging"), \
             patch("main.db.init_db"), \
             patch("main.BinanceClient", return_value=Mock()), \
             patch("main.load_usdt_perpetual_symbols", side_effect=BinanceAPIError("boom")), \
             patch("main.time.sleep", side_effect=KeyboardInterrupt):
            main.main()


if __name__ == "__main__":
    unittest.main()
