"""
strategy/trend_health.py
--------------------------
Post-pullback trend health monitoring (requirement 10/11).

After an alert fires, a token often pulls back before continuing. This
module re-checks an already-alerted symbol and asks: "after dipping from
the alert price, is the setup still structurally healthy?" It looks at
whether OI is still holding up, volume is still active, price is holding
MA7/MA25, and funding hasn't flipped hostile.

This is intentionally a simple, explainable point system (not a second
full strategy run) - see `evaluate_trend_health()`.
"""

import logging

import config
from data.binance_client import BinanceClient
from data.candles import get_closed_candles_df
from data.funding import get_funding_rate
from data.open_interest import get_open_interest_change
from strategy.indicators import build_indicator_snapshot

logger = logging.getLogger(__name__)


def evaluate_trend_health(client: BinanceClient, alert_row: dict) -> dict | None:
    """
    `alert_row` is expected to contain at least:
        symbol, price (the alert-time price), open_interest (alert-time OI)

    Returns None if there hasn't been a meaningful pullback yet (nothing to
    evaluate), otherwise returns a dict with all the trend_health_updates
    fields:
      {
        "pullback_depth_pct": float,
        "oi_after_pullback": float,
        "oi_change_after_pullback_pct": float | None,
        "volume_after_pullback_ratio": float | None,
        "price_holding_ma7": bool | None,
        "price_holding_ma25": bool | None,
        "funding_after_pullback": float,
        "trend_health_score": int,
        "continuation_status": "healthy" | "neutral" | "weak",
      }
    """
    symbol = alert_row["symbol"]
    alert_price = alert_row["price"]
    alert_oi = alert_row.get("open_interest")

    df = get_closed_candles_df(
        client, symbol=symbol, interval=config.TIMEFRAME, limit=config.CANDLE_LIMIT
    )
    if df.empty or len(df) < 10:
        return None

    indicators = build_indicator_snapshot(df)
    current_price = indicators["current_price"]

    pullback_depth_pct = 0.0
    if alert_price and current_price < alert_price:
        pullback_depth_pct = ((alert_price - current_price) / alert_price) * 100

    if pullback_depth_pct < config.MIN_PULLBACK_PCT_TO_EVALUATE:
        # Price hasn't pulled back meaningfully yet - nothing to evaluate.
        return None

    funding_after_pullback = get_funding_rate(client, symbol)
    oi_info = get_open_interest_change(client, symbol)
    oi_after_pullback = oi_info["current_oi"]

    oi_change_after_pullback_pct = None
    if alert_oi and alert_oi > 0:
        oi_change_after_pullback_pct = ((oi_after_pullback - alert_oi) / alert_oi) * 100

    volume_after_pullback_ratio = indicators["volume_ratio"]
    price_holding_ma7 = indicators["holding_ma7"]
    price_holding_ma25 = indicators["holding_ma25"]

    # --- Composite trend health score (0-100) ---------------------------
    score = 0
    if oi_change_after_pullback_pct is not None and oi_change_after_pullback_pct >= 0:
        score += 30  # OI held or grew despite the pullback
    if volume_after_pullback_ratio is not None and volume_after_pullback_ratio >= 1.0:
        score += 20  # volume still active, not drying up
    if price_holding_ma7:
        score += 25
    if price_holding_ma25:
        score += 25

    if score >= config.TREND_HEALTH_UPDATE_THRESHOLD:
        continuation_status = "healthy"
    elif score >= 40:
        continuation_status = "neutral"
    else:
        continuation_status = "weak"

    return {
        "pullback_depth_pct": pullback_depth_pct,
        "oi_after_pullback": oi_after_pullback,
        "oi_change_after_pullback_pct": oi_change_after_pullback_pct,
        "volume_after_pullback_ratio": volume_after_pullback_ratio,
        "price_holding_ma7": price_holding_ma7,
        "price_holding_ma25": price_holding_ma25,
        "funding_after_pullback": funding_after_pullback,
        "trend_health_score": score,
        "continuation_status": continuation_status,
    }
