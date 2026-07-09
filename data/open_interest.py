"""
data/open_interest.py
----------------------
Fetches current open interest (OI) for a symbol and compares it against the
most recent OI reading stored in `open_interest_snapshots`, producing a
percentage change. Binance's public API only exposes a point-in-time OI
value (not a delta), so the bot keeps its own history in SQLite to derive
"OI rising" over time.
"""

import logging

from data.binance_client import BinanceClient
from database import db

logger = logging.getLogger(__name__)


def _get_history_change_pct(client: BinanceClient, symbol: str, period: str) -> tuple[float | None, bool]:
    raw_history = client.get_open_interest_history(symbol=symbol, period=period, limit=2)
    if not raw_history or len(raw_history) < 2:
        return None, False

    try:
        current = float(raw_history[-1].get("sumOpenInterest", 0))
        previous = float(raw_history[-2].get("sumOpenInterest", 0))
    except (TypeError, ValueError):
        return None, False

    if previous <= 0:
        return None, False

    change_pct = ((current - previous) / previous) * 100
    return change_pct, current > previous


def get_open_interest_change(client: BinanceClient, symbol: str) -> dict:
    """
    Returns a dict with current OI plus 15m/30m/1h change percentages.
    These values are used for the hard-filter stage and later scoring.
    """
    raw = client.get_open_interest(symbol)
    current_oi = float(raw["openInterest"])
    logger.debug("%s: OI fetched (%.2f)", symbol, current_oi)

    oi_15m_change_pct, oi_15m_increased = _get_history_change_pct(client, symbol, "15m")
    oi_30m_change_pct, oi_30m_increased = _get_history_change_pct(client, symbol, "30m")
    oi_1h_change_pct, oi_1h_increased = _get_history_change_pct(client, symbol, "1h")

    db.save_open_interest_snapshot(symbol, current_oi)

    return {
        "current_oi": current_oi,
        "previous_oi": None,
        "oi_change_pct": oi_1h_change_pct,
        "oi_increased": oi_1h_increased,
        "oi_15m_change_pct": oi_15m_change_pct,
        "oi_30m_change_pct": oi_30m_change_pct,
        "oi_1h_change_pct": oi_1h_change_pct,
        "oi_15m_increased": oi_15m_increased,
        "oi_30m_increased": oi_30m_increased,
        "oi_1h_increased": oi_1h_increased,
    }
