"""
strategy/outcome_tracker.py
-----------------------------
Result tracking for alerts (requirement 9).

After an alert is saved, the database schedules four "checkpoint" rows in
`alert_outcomes` (15m / 1h / 4h / 24h - see database/models.py). This
module resolves those checkpoints once they come due.

CURRENT IMPLEMENTATION STATUS:
  - `price_change_pct` is fully computed: we fetch the current mark price
    and compare it to the alert-time price.
  - `max_gain_pct`, `max_drawdown_pct`, `hit_tp1..hit_tp4`, and `hit_stop`
    are intentionally left as stub placeholders (None / not computed).
    Computing them properly requires continuously sampling price between
    the alert and the checkpoint (not just at the checkpoint moment) and
    requires defined TP/SL levels, which are out of scope for v0.1. The
    database columns exist and are ready for that logic to be added later
    (e.g. by recording high/low from each closed candle between alert time
    and checkpoint time).

This function is safe to call every scan loop - it only acts on checkpoints
that are actually due, and does nothing if there are none.
"""

import logging

from data.binance_client import BinanceClient, BinanceAPIError
from database import db

logger = logging.getLogger(__name__)


def resolve_due_outcomes(client: BinanceClient) -> int:
    """
    Finds all alert_outcomes rows that are due and not yet recorded,
    fetches the current price for each symbol, and records price_change_pct.

    Returns the number of outcome rows successfully resolved.
    """
    due_outcomes = db.get_due_outcomes()
    resolved = 0

    for outcome in due_outcomes:
        symbol = outcome["symbol"]
        alert_price = outcome["alert_price"]

        try:
            info = client.get_premium_index(symbol)
            current_price = float(info["markPrice"])
        except (BinanceAPIError, KeyError, ValueError) as exc:
            logger.warning("%s: could not resolve outcome checkpoint %s (%s)",
                           symbol, outcome["checkpoint"], exc)
            continue

        price_change_pct = None
        if alert_price:
            price_change_pct = ((current_price - alert_price) / alert_price) * 100

        db.record_alert_outcome(
            outcome_id=outcome["id"],
            price_at_checkpoint=current_price,
            price_change_pct=price_change_pct,
        )
        resolved += 1
        logger.info(
            "%s: outcome checkpoint %s recorded (%.2f%%)",
            symbol, outcome["checkpoint"], price_change_pct if price_change_pct is not None else 0.0,
        )

    return resolved
