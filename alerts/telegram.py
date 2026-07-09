"""
alerts/telegram.py
--------------------
Formats bot output into Telegram messages and sends them via the Telegram
Bot API (https://core.telegram.org/bots/api#sendmessage).

Two message types:
  - format_alert_message / send: the initial bullish-setup alert
  - format_trend_health_update_message / send: a follow-up "still healthy
    after pullback" update for a symbol that was already alerted

Only outgoing "sendMessage" calls are made here - this module never reads
Telegram updates or accepts commands, keeping the bot one-directional and
simple, and strictly read-only with respect to any exchange.
"""

import logging

import requests

import config

logger = logging.getLogger(__name__)


def _display_symbol(symbol: str) -> str:
    # Binance futures symbols look like "BTCUSDT" -> display as "BTC/USDT".
    return symbol.replace(config.QUOTE_ASSET, f"/{config.QUOTE_ASSET}")


def _ma_status_line(evaluation: dict) -> str:
    def mark(holding):
        if holding is None:
            return "n/a"
        return "above" if holding else "below"

    return (
        f"MA7: {mark(evaluation.get('holding_ma7'))} | "
        f"MA25: {mark(evaluation.get('holding_ma25'))} | "
        f"MA99: {mark(evaluation.get('holding_ma99'))}"
    )


def format_alert_message(evaluation: dict, timestamp_utc: str) -> str:
    """
    Builds the human-readable alert text, including score, setup type,
    score breakdown, reasons, risks, funding, OI change, volume ratio,
    MA7/MA25/MA99 status, and the UTC timestamp.
    """
    symbol = evaluation["symbol"]
    display_symbol = _display_symbol(symbol)

    price = evaluation["price"]
    score = evaluation["score"]
    setup_type = evaluation.get("setup_type", "n/a")
    breakdown = evaluation.get("score_breakdown", {})
    volume_ratio = evaluation.get("volume_ratio")
    oi_change_pct = evaluation.get("oi_change_pct")
    funding_rate = evaluation.get("funding_rate")
    reasons = evaluation.get("reasons", [])
    risks = evaluation.get("risks", [])

    volume_ratio_str = f"{volume_ratio:.1f}x" if volume_ratio is not None else "n/a"
    oi_change_str = f"{oi_change_pct:+.1f}%" if oi_change_pct is not None else "n/a"
    funding_str = f"{funding_rate * 100:+.3f}%" if funding_rate is not None else "n/a"

    breakdown_lines = "\n".join(
        f"  {key}: {value:+d}" if isinstance(value, int) else f"  {key}: {value}"
        for key, value in breakdown.items()
    ) or "  n/a"

    reasons_block = "\n".join(f"✅ {r}" for r in reasons) if reasons else "n/a"
    risks_block = "\n".join(f"⚠️ {r}" for r in risks) if risks else "None flagged"

    message = (
        f"🚨 {display_symbol} — Score {score}/100\n"
        f"Setup: {setup_type}\n"
        f"Timeframe: {evaluation.get('timeframe', 'n/a')}\n"
        f"Timestamp (UTC): {timestamp_utc}\n"
        f"\n"
        f"Price: {price:g}\n"
        f"{_ma_status_line(evaluation)}\n"
        f"Volume Ratio: {volume_ratio_str}\n"
        f"OI Change: {oi_change_str}\n"
        f"Funding: {funding_str}\n"
        f"\n"
        f"Score Breakdown:\n{breakdown_lines}\n"
        f"\n"
        f"Reasons:\n{reasons_block}\n"
        f"\n"
        f"Risks:\n{risks_block}"
    )
    return message


def format_trend_health_update_message(symbol: str, health: dict) -> str:
    """
    Builds a short continuation/update message, e.g.:
    "ALICEUSDT Update: Pullback healthy. OI still strong, volume holding,
    price holding MA7/MA25. Continuation score: 86/100."
    """
    display_symbol = _display_symbol(symbol)
    status = health["continuation_status"]
    score = health["trend_health_score"]

    holding_bits = []
    if health.get("price_holding_ma7"):
        holding_bits.append("MA7")
    if health.get("price_holding_ma25"):
        holding_bits.append("MA25")
    holding_str = "/".join(holding_bits) if holding_bits else "neither MA"

    oi_change = health.get("oi_change_after_pullback_pct")
    oi_str = f"OI {oi_change:+.1f}%" if oi_change is not None else "OI n/a"

    volume_ratio = health.get("volume_after_pullback_ratio")
    volume_str = f"volume {volume_ratio:.1f}x avg" if volume_ratio is not None else "volume n/a"

    status_label = {"healthy": "Pullback healthy", "neutral": "Pullback neutral", "weak": "Pullback weak"}.get(
        status, status
    )

    return (
        f"{display_symbol} Update: {status_label}. "
        f"{oi_str}, {volume_str}, price holding {holding_str}. "
        f"Pullback depth: {health['pullback_depth_pct']:.1f}%. "
        f"Continuation score: {score}/100."
    )


def _send(message: str) -> bool:
    """Low-level send. Returns True on success, False otherwise."""
    bot_token = (config.TELEGRAM_BOT_TOKEN or "").strip()
    chat_id = (config.TELEGRAM_CHAT_ID or "").strip()
    if not bot_token or not chat_id or bot_token.startswith("your_") or chat_id.startswith("your_"):
        logger.warning("Skipping Telegram send: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not configured.")
        return False

    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": config.TELEGRAM_CHAT_ID,
        "text": message,
        "disable_web_page_preview": True,
    }

    try:
        response = requests.post(url, json=payload, timeout=config.REQUEST_TIMEOUT_SECONDS)
        if response.status_code == 200:
            return True
        logger.error("Telegram send failed: HTTP %s %s", response.status_code, response.text[:300])
        return False
    except requests.RequestException as exc:
        logger.error("Telegram network error: %s", exc)
        return False


def send_telegram_alert(message: str) -> bool:
    """Sends an initial setup alert message."""
    return _send(message)


def send_telegram_update(message: str) -> bool:
    """Sends a post-pullback continuation update message."""
    return _send(message)
