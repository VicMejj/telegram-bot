"""
main.py
--------
Entry point for Crypto Edge Bot v0.1.

What this script does, in order, on every loop:
  1. Load all Binance USDT-M perpetual futures symbols.
  2. For each symbol: fetch the last FULLY CLOSED candle's data, compute
     indicators, fetch funding rate, fetch open interest and compare it to
     the last stored value, and score the setup. Every scan is saved to
     `market_snapshots`, whether or not it alerts.
  3. If the score meets SCORE_THRESHOLD AND the symbol is not in its 24h
     alert cooldown, send a Telegram alert and save the alert (with its
     score breakdown, reasons, and risks) to SQLite. Future outcome
     checkpoints (15m/1h/4h/24h) are scheduled automatically.
  4. Resolve any outcome checkpoints that have come due.
  5. Re-check recently-alerted symbols for post-pullback trend health, and
     send a follow-up Telegram update if a pullback looks healthy.
  6. Sleep for SCAN_INTERVAL_SECONDS and repeat.

IMPORTANT: This bot is READ-ONLY with respect to trading. It never places,
modifies, or cancels orders, never touches margin/leverage endpoints, and
never uses a private/trading API key - only Binance's PUBLIC futures
endpoints are called (see data/binance_client.py).
"""

import logging
import sys
import time
from datetime import datetime, timezone, timedelta

import config
import logging_setup
from data.binance_client import BinanceClient, BinanceAPIError
from strategy.ma_oi_funding_strategy import evaluate_symbol
from strategy.trend_health import evaluate_trend_health
from strategy.outcome_tracker import resolve_due_outcomes
from alerts.telegram import (
    format_alert_message,
    send_telegram_alert,
    format_trend_health_update_message,
    send_telegram_update,
)
from database import db

logger = logging.getLogger(__name__)


def load_usdt_perpetual_symbols(client: BinanceClient) -> list:
    """
    Returns a list of symbol names (e.g. ["BTCUSDT", "ETHUSDT", ...]) for
    all TRADING, PERPETUAL contracts quoted in the configured quote asset.
    """
    info = client.get_exchange_info()
    symbols = []
    for s in info.get("symbols", []):
        if (
            s.get("quoteAsset") == config.QUOTE_ASSET
            and s.get("contractType") == "PERPETUAL"
            and s.get("status") == "TRADING"
        ):
            symbols.append(s["symbol"])
    return sorted(symbols)


def process_symbol(client: BinanceClient, symbol: str) -> dict | None:
    """
    Evaluates one symbol, saves its market snapshot, and sends + saves an
    alert if it qualifies and isn't in cooldown. Returns the evaluation
    dict (or None if there wasn't enough data).
    """
    evaluation = evaluate_symbol(client, symbol)
    if evaluation is None:
        return None

    db.save_market_snapshot(evaluation)
    logger.debug("%s: market snapshot saved (score=%d)", symbol, evaluation["score"])

    if not evaluation.get("passes_hard_filters", False):
        reasons = evaluation.get("hard_filter_reasons", ["hard filters failed"])
        logger.info(
            "%s: alert skipped (%s)",
            symbol,
            "; ".join(reasons),
        )
        return evaluation

    if evaluation["score"] >= config.SCORE_THRESHOLD:
        if db.is_symbol_in_cooldown(symbol):
            logger.info("%s: alert skipped (cooldown active, score=%d)", symbol, evaluation["score"])
        else:
            timestamp_utc = datetime.now(timezone.utc).isoformat()
            message = format_alert_message(evaluation, timestamp_utc)
            sent = send_telegram_alert(message)
            alert_id = db.save_alert(evaluation)
            logger.info(
                "%s: ALERT %s (score=%d, alert_id=%d) - database saved",
                symbol, "sent" if sent else "send FAILED", evaluation["score"], alert_id,
            )

    return evaluation


def run_single_scan(client: BinanceClient, symbols: list) -> None:
    """Evaluates every symbol once."""
    alert_eligible_count = 0

    for i, symbol in enumerate(symbols, start=1):
        try:
            evaluation = process_symbol(client, symbol)
        except BinanceAPIError as exc:
            logger.error("[%d/%d] %s: skipped due to data fetch error (%s)", i, len(symbols), symbol, exc)
            time.sleep(config.SYMBOL_SCAN_DELAY_SECONDS)
            continue
        except Exception as exc:  # noqa: BLE001 - keep the scan loop alive
            logger.exception("[%d/%d] %s: unexpected error", i, len(symbols), symbol)
            time.sleep(config.SYMBOL_SCAN_DELAY_SECONDS)
            continue

        if evaluation is None:
            logger.debug("[%d/%d] %s: not enough closed-candle data, skipped", i, len(symbols), symbol)
        else:
            logger.debug("[%d/%d] %s: score=%d", i, len(symbols), symbol, evaluation["score"])
            if evaluation["score"] >= config.SCORE_THRESHOLD:
                alert_eligible_count += 1

        time.sleep(config.SYMBOL_SCAN_DELAY_SECONDS)

    logger.info("Scan complete. %d/%d symbols met the score threshold this pass.",
                alert_eligible_count, len(symbols))


def run_trend_health_monitor(client: BinanceClient) -> None:
    """
    Re-checks recently-alerted symbols for post-pullback trend health and
    sends a Telegram update for any that look healthy (requirement 11).
    """
    candidates = db.get_alerts_needing_health_check()
    if not candidates:
        return

    logger.info("Trend health monitor: checking %d recent alert(s)...", len(candidates))
    for alert_row in candidates:
        symbol = alert_row["symbol"]
        try:
            health = evaluate_trend_health(client, alert_row)
        except BinanceAPIError as exc:
            logger.error("%s: trend health check failed (%s)", symbol, exc)
            continue
        except Exception:
            logger.exception("%s: unexpected error during trend health check", symbol)
            continue

        if health is None:
            continue  # no meaningful pullback yet, nothing to record

        # Compare to the previous trend-health check for this alert (if any)
        last = db.get_last_trend_health_update(alert_row["id"])

        should_send = False

        # First-time: if no previous update, only send if score clears threshold
        if last is None:
            if (
                health["trend_health_score"] >= config.TREND_HEALTH_UPDATE_THRESHOLD
                and not db.was_update_alert_recently_sent(alert_row["id"])
            ):
                should_send = True
        else:
            prev_score = last.get("trend_health_score") or 0
            prev_status = last.get("continuation_status")

            # Send if score changed significantly, or continuation status changed,
            # or the score crossed the threshold upward for the first time.
            score_diff = abs(health["trend_health_score"] - prev_score)
            crossed_threshold = (
                prev_score < config.TREND_HEALTH_UPDATE_THRESHOLD
                and health["trend_health_score"] >= config.TREND_HEALTH_UPDATE_THRESHOLD
            )

            if (
                score_diff >= 10
                or health["continuation_status"] != prev_status
                or crossed_threshold
            ) and not db.was_update_alert_recently_sent(alert_row["id"]):
                should_send = True

        update_id = db.save_trend_health_update(alert_row["id"], symbol, health)

        if should_send:
            message = format_trend_health_update_message(symbol, health)
            sent = send_telegram_update(message)
            if sent:
                db.mark_update_alert_sent(update_id)
            logger.info("%s: trend health update %s", symbol, "sent" if sent else "send FAILED")


def main() -> None:
    logging_setup.configure_logging()
    logger.info("Crypto Edge Bot v0.1 starting up...")

    problems = config.validate_config()
    if problems:
        logger.warning("Configuration warning(s):")
        for p in problems:
            logger.warning("  - %s", p)
        logger.warning("The bot will still run and log to console, but Telegram "
                        "alerts will not be sent until this is fixed.")

    logger.info("Initializing database...")
    db.init_db()

    client = BinanceClient()

    logger.info("Loading USDT perpetual futures symbols...")
    try:
        symbols = load_usdt_perpetual_symbols(client)
    except BinanceAPIError as exc:
        logger.error("Failed to load symbols from Binance: %s", exc)
        sys.exit(1)

    if config.MAX_SYMBOLS > 0:
        symbols = symbols[: config.MAX_SYMBOLS]

    logger.info(
        "Symbols loaded: %d. Timeframe=%s, FAST_MA=%d, MID_MA=%d, SLOW_MA=%d, "
        "Score threshold=%d, Cooldown=%.1fh, Scan interval=%ds",
        len(symbols), config.TIMEFRAME, config.FAST_MA, config.MID_MA, config.SLOW_MA,
        config.SCORE_THRESHOLD, config.ALERT_COOLDOWN_HOURS, config.SCAN_INTERVAL_SECONDS,
    )

    try:
        while True:
            logger.info("=" * 60)
            logger.info("Starting scan...")
            run_single_scan(client, symbols)

            try:
                resolved = resolve_due_outcomes(client)
                if resolved:
                    logger.info("Resolved %d due outcome checkpoint(s).", resolved)
            except Exception:
                logger.exception("Error while resolving outcome checkpoints")

            try:
                run_trend_health_monitor(client)
            except Exception:
                logger.exception("Error while running trend health monitor")

            # If the configured scan interval equals the 30-minute default,
            # synchronize scans to :00 and :30 UTC. Otherwise, honor the
            # configured interval literally.
            if config.SCAN_INTERVAL_SECONDS == 1800:
                now = datetime.now(timezone.utc)
                if now.minute < 30:
                    target = now.replace(minute=30, second=0, microsecond=0)
                else:
                    target = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
                sleep_secs = int((target - now).total_seconds())
                if sleep_secs <= 0:
                    sleep_secs = config.SCAN_INTERVAL_SECONDS
                elif sleep_secs > config.SCAN_INTERVAL_SECONDS:
                    sleep_secs = config.SCAN_INTERVAL_SECONDS
            else:
                sleep_secs = config.SCAN_INTERVAL_SECONDS

            logger.info("Sleeping for %d seconds...", sleep_secs)
            time.sleep(sleep_secs)
    except KeyboardInterrupt:
        logger.info("Stopped by user. Goodbye!")


if __name__ == "__main__":
    main()
