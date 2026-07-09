"""
data/candles.py
----------------
Helpers for fetching candlestick (kline) data and turning it into a clean
pandas DataFrame ready for indicator calculations.

CRITICAL RULE: signals must only ever be computed from the last FULLY
CLOSED candle, never the currently-forming one. Binance's klines endpoint
normally includes the in-progress candle as the last row, so
`get_closed_candles_df()` strips it off before returning.

Example: if the strategy timeframe is 1h and it is currently 14:25 UTC,
the candle covering 14:00-15:00 is still forming. The bot should use the
candle that closed at 14:00 UTC (covering 13:00-14:00), not the partial
14:00-15:00 candle. That trim happens here.
"""

import logging

import pandas as pd

from data.binance_client import BinanceClient

logger = logging.getLogger(__name__)

COLUMNS = [
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_asset_volume", "num_trades",
    "taker_buy_base_vol", "taker_buy_quote_vol", "ignore",
]

NUMERIC_COLUMNS = ["open", "high", "low", "close", "volume"]


def fetch_candles_df(client: BinanceClient, symbol: str, interval: str, limit: int) -> pd.DataFrame:
    """
    Fetches raw klines for `symbol` and returns a DataFrame sorted
    oldest -> newest with proper numeric dtypes. This DataFrame MAY still
    include a currently-forming candle as its last row - use
    `get_closed_candles_df` (below) if you need only closed candles.
    """
    raw = client.get_klines(symbol=symbol, interval=interval, limit=limit)
    if not raw:
        return pd.DataFrame(columns=COLUMNS)

    df = pd.DataFrame(raw, columns=COLUMNS)
    df[NUMERIC_COLUMNS] = df[NUMERIC_COLUMNS].astype(float)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
    df["close_time"] = pd.to_datetime(df["close_time"], unit="ms")
    return df


def get_closed_candles_df(client: BinanceClient, symbol: str, interval: str, limit: int) -> pd.DataFrame:
    """
    Fetches candles and strips off the currently-forming (not yet closed)
    candle, if present. All downstream signal/indicator calculations should
    use the DataFrame returned by this function, never the raw one.
    """
    df = fetch_candles_df(client, symbol=symbol, interval=interval, limit=limit)
    if df.empty:
        return df

    now_utc = pd.Timestamp.now("UTC").tz_localize(None)
    last_close_time = df.iloc[-1]["close_time"]

    if last_close_time > now_utc:
        # The last row hasn't closed yet - drop it.
        df = df.iloc[:-1].reset_index(drop=True)
        logger.debug(
            "%s: dropped still-forming candle (would close at %s, now is %s)",
            symbol, last_close_time, now_utc,
        )

    return df
