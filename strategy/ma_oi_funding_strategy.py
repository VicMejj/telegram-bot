"""
strategy/ma_oi_funding_strategy.py
------------------------------------
The main strategy: MA breakout + Open Interest + Funding Rate.

Ties together candles, funding, and open-interest data for a single symbol,
computes indicators from the last FULLY CLOSED candle only, scores the
setup, and returns a single "evaluation" dict that the rest of the app
(alerts, database, trend-health monitor) can consume.

Contains no networking of its own - it delegates raw data fetching to the
`data` package, keeping responsibilities separate.
"""

import logging

import config
from data.binance_client import BinanceClient
from database import db
from data.candles import get_closed_candles_df
from data.funding import get_funding_rate
from data.open_interest import get_open_interest_change
from strategy.indicators import build_indicator_snapshot
from strategy.scoring import passes_hard_filters, score_setup

logger = logging.getLogger(__name__)


def evaluate_symbol(client: BinanceClient, symbol: str) -> dict | None:
    """
    Runs the full pipeline for one symbol:
      1. Fetch candles, drop the currently-forming one, compute
         MA7/MA25/MA99, volume ratio, extension, MA-holding status
      2. Fetch funding rate
      3. Fetch open interest and compare to the last stored reading
      4. Score the setup (breakdown + reasons + risks + setup type)

    Returns a dict with everything needed for an alert/DB row, or None if
    there wasn't enough closed-candle history to evaluate the symbol.
    """
    df = get_closed_candles_df(
        client, symbol=symbol, interval=config.TIMEFRAME, limit=config.CANDLE_LIMIT
    )
    if df.empty or len(df) < 10:
        # Not enough closed-candle history (e.g. a brand new listing).
        return None

    logger.debug("%s: candle data fetched (%d closed candles, timeframe=%s)",
                 symbol, len(df), config.TIMEFRAME)

    indicators = build_indicator_snapshot(df)
    indicators["symbol"] = symbol
    funding_rate = get_funding_rate(client, symbol)
    oi_info = get_open_interest_change(client, symbol)

    history_rows = db.get_recent_alert_history_rows()
    filter_passed, filter_reasons = passes_hard_filters(indicators, funding_rate, oi_info)

    # Preserve each stage's result for aggregate scan reporting. This does
    # not change the hard-filter or scoring decisions below.
    ma_filter_passed = (
        indicators.get("current_price") is not None
        and indicators.get("ma7") is not None
        and indicators.get("ma25") is not None
        and indicators.get("ma99") is not None
        and indicators["current_price"] > indicators["ma7"]
        and indicators["ma7"] > indicators["ma25"]
        and indicators["ma25"] > indicators["ma99"]
    )
    volume_filter_passed = (
        indicators.get("volume_ratio") is not None
        and indicators["volume_ratio"] >= config.VOLUME_RATIO_MIN
    )
    oi_15m = oi_info.get("oi_15m_change_pct")
    oi_30m = oi_info.get("oi_30m_change_pct")
    oi_1h = oi_info.get("oi_1h_change_pct")
    oi_filter_passed = (
        oi_15m is not None
        and oi_30m is not None
        and oi_1h is not None
        and oi_15m >= config.OI_15M_MIN_INCREASE_PCT
        and oi_30m >= config.OI_30M_MIN_INCREASE_PCT
        and oi_1h >= config.OI_1H_MIN_INCREASE_PCT
        and oi_30m > oi_15m
        and oi_1h > oi_30m
    )
    funding_filter_passed = (
        funding_rate is not None
        and funding_rate <= config.FUNDING_HEALTHY_THRESHOLD
    )

    if not filter_passed:
        scoring = {
            "final_score": 0,
            "score_breakdown": {
                "ma_trend_score": 0,
                "volume_score": 0,
                "oi_score": 0,
                "funding_score": 0,
                "structure_score": 0,
                "risk_penalty": 0,
                "final_score": 0,
            },
            "reasons": filter_reasons,
            "risks": [],
            "setup_type": "Rejected by filters",
        }
    else:
        scoring = score_setup(indicators, funding_rate, oi_info, history_rows=history_rows)

    return {
        "symbol": symbol,
        "timeframe": config.TIMEFRAME,
        "price": indicators["current_price"],
        "ma7": indicators["ma7"],
        "ma25": indicators["ma25"],
        "ma99": indicators["ma99"],
        "holding_ma7": indicators["holding_ma7"],
        "holding_ma25": indicators["holding_ma25"],
        "holding_ma99": indicators["holding_ma99"],
        "volume_ratio": indicators["volume_ratio"],
        "is_overextended": indicators["is_overextended"],
        "funding_rate": funding_rate,
        "open_interest": oi_info["current_oi"],
        "oi_change_pct": oi_info.get("oi_change_pct"),
        "oi_increased": oi_info.get("oi_increased", False),
        "oi_15m_change_pct": oi_info.get("oi_15m_change_pct"),
        "oi_30m_change_pct": oi_info.get("oi_30m_change_pct"),
        "oi_1h_change_pct": oi_info.get("oi_1h_change_pct"),
        "passes_hard_filters": filter_passed,
        "ma_filter_passed": ma_filter_passed,
        "volume_filter_passed": volume_filter_passed,
        "oi_filter_passed": oi_filter_passed,
        "funding_filter_passed": funding_filter_passed,
        "hard_filter_reasons": filter_reasons,
        "score": scoring["final_score"],
        "score_breakdown": scoring["score_breakdown"],
        "reasons": scoring["reasons"],
        "risks": scoring["risks"],
        "setup_type": scoring["setup_type"],
    }
