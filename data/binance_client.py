"""
data/binance_client.py
-----------------------
Thin wrapper around Binance's public USDT-M Futures REST API.

This module only touches PUBLIC endpoints (exchange info, klines, open
interest, premium index / funding rate). No API key or secret is required
and no orders are ever placed. It centralizes:
  - the base URL
  - a shared requests.Session (connection reuse)
  - basic retry/backoff logic
  - JSON error handling

Every other module that needs Binance data should go through this client
instead of calling `requests` directly, so retry/error handling stays
consistent in one place.
"""

import logging
import time
import requests

import config

logger = logging.getLogger(__name__)


class BinanceAPIError(Exception):
    """Raised when Binance returns an error response or the request fails
    after all retries have been exhausted."""
    pass


class BinanceClient:
    def __init__(self, base_url: str = config.BINANCE_FAPI_BASE_URL):
        self.base_url = base_url
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "crypto-edge-bot/0.1"})

    def _get(self, path: str, params: dict | None = None) -> dict | list:
        """
        Perform a GET request against the Futures API with simple retry
        logic. Raises BinanceAPIError if all attempts fail.
        """
        url = f"{self.base_url}{path}"
        last_error = None

        for attempt in range(1, config.REQUEST_MAX_RETRIES + 1):
            try:
                response = self.session.get(
                    url, params=params, timeout=config.REQUEST_TIMEOUT_SECONDS
                )
                if response.status_code == 200:
                    return response.json()

                # Binance returns 4xx/5xx with a JSON error body sometimes.
                last_error = (
                    f"HTTP {response.status_code} calling {path}: {response.text[:300]}"
                )
            except requests.RequestException as exc:
                last_error = f"Network error calling {path}: {exc}"

            # Back off before retrying (except after the last attempt).
            if attempt < config.REQUEST_MAX_RETRIES:
                time.sleep(config.REQUEST_RETRY_BACKOFF_SECONDS * attempt)

        logger.error("Data fetch error: %s", last_error)
        raise BinanceAPIError(last_error)

    # -- Public endpoints ---------------------------------------------------

    def get_exchange_info(self) -> dict:
        """Returns full exchange info, including all tradable symbols."""
        return self._get("/fapi/v1/exchangeInfo")

    def get_klines(self, symbol: str, interval: str, limit: int = 200) -> list:
        """
        Returns raw kline (candlestick) data for a symbol.
        Each row is: [open_time, open, high, low, close, volume,
                       close_time, quote_asset_volume, num_trades,
                       taker_buy_base_vol, taker_buy_quote_vol, ignore]
        """
        params = {"symbol": symbol, "interval": interval, "limit": limit}
        return self._get("/fapi/v1/klines", params=params)

    def get_open_interest(self, symbol: str) -> dict:
        """Returns current open interest for a symbol."""
        return self._get("/fapi/v1/openInterest", params={"symbol": symbol})

    def get_open_interest_history(self, symbol: str, period: str, limit: int = 2) -> list:
        """Returns historical OI samples for a symbol at the requested period."""
        params = {"symbol": symbol, "period": period, "limit": limit}
        return self._get("/fapi/v1/openInterestHist", params=params)

    def get_premium_index(self, symbol: str) -> dict:
        """
        Returns mark price info, which includes `lastFundingRate` -
        the most recently applied funding rate for the symbol.
        """
        return self._get("/fapi/v1/premiumIndex", params={"symbol": symbol})
