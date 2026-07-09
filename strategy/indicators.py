"""
strategy/indicators.py
-----------------------
Pure calculation functions that turn a CLOSED-CANDLES-ONLY DataFrame into
the technical indicators used by the strategy: MA7, MA25, MA99, volume
ratio, MA trend status, and "overextension" relative to MA99.

These functions do not fetch data or know about Binance/Telegram/SQLite -
they just take a DataFrame in and return numbers out, which makes them easy
to unit test. The caller is responsible for making sure `df` only contains
fully closed candles (see data/candles.get_closed_candles_df) - these
functions always treat the LAST row as the most recent closed candle.

Moving-average periods are configurable via config.FAST_MA / MID_MA /
SLOW_MA (defaults 7 / 25 / 99), but the resulting dict keys stay
"ma7" / "ma25" / "ma99" for readability throughout the rest of the codebase.
"""

import pandas as pd

import config


def compute_moving_averages(df: pd.DataFrame) -> dict:
    """
    Computes simple moving averages of the close price using the
    configurable periods FAST_MA / MID_MA / SLOW_MA.
    Returns the most recent ma7/ma25/ma99 values (or None if there isn't
    enough closed-candle history yet for a given window).
    """
    periods = {
        "ma7": config.FAST_MA,
        "ma25": config.MID_MA,
        "ma99": config.SLOW_MA,
    }
    result = {}
    for key, window in periods.items():
        if len(df) >= window:
            result[key] = float(df["close"].rolling(window=window).mean().iloc[-1])
        else:
            result[key] = None
    return result


def compute_volume_ratio(df: pd.DataFrame, avg_window: int = config.VOLUME_AVG_WINDOW) -> dict:
    """
    Compares the most recent CLOSED candle's volume (the last row of `df`)
    to the average volume of the preceding `avg_window` closed candles.

    Returns {"current_volume": float, "average_volume": float | None,
             "volume_ratio": float | None}
    """
    if df.empty:
        return {"current_volume": None, "average_volume": None, "volume_ratio": None}

    current_volume = float(df["volume"].iloc[-1])

    history = df["volume"].iloc[:-1]
    if history.empty:
        average_volume = None
    elif len(history) < avg_window:
        average_volume = float(history.mean())
    else:
        average_volume = float(history.tail(avg_window).mean())

    volume_ratio = None
    if average_volume and average_volume > 0:
        volume_ratio = current_volume / average_volume

    return {
        "current_volume": current_volume,
        "average_volume": average_volume,
        "volume_ratio": volume_ratio,
    }


def compute_extension(current_price: float, ma99: float | None) -> dict:
    """
    Measures how far the current price has run above MA99, as a fraction.
    E.g. 0.20 means price is 20% above MA99.

    Returns {"extension_pct": float | None, "is_overextended": bool}
    """
    if ma99 is None or ma99 <= 0:
        return {"extension_pct": None, "is_overextended": False}

    extension_pct = (current_price - ma99) / ma99
    is_overextended = extension_pct > config.MAX_EXTENSION_ABOVE_MA99
    return {"extension_pct": extension_pct, "is_overextended": is_overextended}


def compute_ma_status(current_price: float, mas: dict) -> dict:
    """
    Returns whether price is currently holding above each moving average,
    e.g. {"holding_ma7": True, "holding_ma25": False, "holding_ma99": True}.
    `None` means the MA couldn't be computed (not enough history).
    """
    status = {}
    for key in ("ma7", "ma25", "ma99"):
        ma_value = mas.get(key)
        status[f"holding_{key}"] = (current_price > ma_value) if ma_value is not None else None
    return status


def build_indicator_snapshot(df: pd.DataFrame) -> dict:
    """
    Convenience function that runs all indicator calculations on a
    CLOSED-CANDLES-ONLY DataFrame and returns a single flat dict, along with
    the current (last closed candle's) price.
    """
    if df.empty:
        raise ValueError("Cannot compute indicators on an empty candles DataFrame.")

    current_price = float(df["close"].iloc[-1])
    mas = compute_moving_averages(df)
    volume_info = compute_volume_ratio(df)
    extension_info = compute_extension(current_price, mas["ma99"])
    ma_status = compute_ma_status(current_price, mas)

    snapshot = {"current_price": current_price}
    snapshot.update(mas)
    snapshot.update(volume_info)
    snapshot.update(extension_info)
    snapshot.update(ma_status)
    return snapshot
