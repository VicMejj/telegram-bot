"""
config.py
---------
Centralized configuration for Crypto Edge Bot.

Every threshold used by the strategy, cooldown, and trend-health logic lives
here (not hardcoded in the modules that use it) so the bot can be tuned by
editing .env instead of source code.

Loads settings from a local .env file via python-dotenv and exposes them as
simple module-level constants that the rest of the app imports.
"""

import os
from dotenv import load_dotenv

# Load variables from a local .env file into the process environment.
# If .env does not exist, this silently does nothing (and os.getenv falls
# back to the defaults below), so the bot still runs with sane defaults.
load_dotenv()


def _get_float(name: str, default: float) -> float:
    val = os.getenv(name)
    try:
        return float(val) if val is not None else default
    except ValueError:
        return default


def _get_int(name: str, default: int) -> int:
    val = os.getenv(name)
    try:
        return int(val) if val is not None else default
    except ValueError:
        return default


# --- Telegram -------------------------------------------------------------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# --- Binance USDT-M Futures (PUBLIC endpoints only, read-only) ------------
# No private/trading endpoints are ever called by this bot. See data/binance_client.py.
BINANCE_FAPI_BASE_URL = "https://fapi.binance.com"
QUOTE_ASSET = os.getenv("QUOTE_ASSET", "USDT")

# --- Moving averages (configurable periods, NOT hardcoded) ----------------
# Field/column names stay ma7 / ma25 / ma99 throughout the codebase for
# readability, but the actual lookback period for each is configurable here.
FAST_MA = _get_int("FAST_MA", 7)
MID_MA = _get_int("MID_MA", 25)
SLOW_MA = _get_int("SLOW_MA", 99)

# --- Strategy timeframe ----------------------------------------------------
# Default is 1h. Only the last FULLY CLOSED candle of this timeframe is ever
# used for signal calculations (see data/candles.get_closed_candles_df).
TIMEFRAME = os.getenv("TIMEFRAME", "1h")

# --- Scan / scoring settings ------------------------------------------------
SCORE_THRESHOLD = _get_int("SCORE_THRESHOLD", 75)          # score needed to alert
SCAN_INTERVAL_SECONDS = _get_int("SCAN_INTERVAL_SECONDS", 1800)
MAX_SYMBOLS = _get_int("MAX_SYMBOLS", 0)                    # 0 = scan all symbols

# Number of candles to request per symbol. Needs to comfortably cover
# SLOW_MA plus the volume-average lookback window, with buffer for the
# forming candle that gets dropped.
VOLUME_AVG_WINDOW = _get_int("VOLUME_AVG_WINDOW", 20)
CANDLE_LIMIT = max(200, SLOW_MA + VOLUME_AVG_WINDOW + 50)

# Minimum open-interest increase (in %) versus the previous stored reading
# to count as "OI rising" for scoring purposes.
OI_MIN_INCREASE_PCT = _get_float("OI_MIN_INCREASE_PCT", 0.0)

# Minimum volume ratio (current closed candle vs trailing average) to
# count as a volume surge.
VOLUME_RATIO_MIN = _get_float("VOLUME_RATIO_MIN", 2.0)

# When enabled, the bot only sends alerts when MA7 > MA25 > MA99.
REQUIRE_MA_ALIGNMENT = _get_int("REQUIRE_MA_ALIGNMENT", 1) == 1

# Minimum positive OI change percentages required for the hard-filter stage.
OI_15M_MIN_INCREASE_PCT = _get_float("OI_15M_MIN_INCREASE_PCT", 2.0)
OI_30M_MIN_INCREASE_PCT = _get_float("OI_30M_MIN_INCREASE_PCT", 3.0)
OI_1H_MIN_INCREASE_PCT = _get_float("OI_1H_MIN_INCREASE_PCT", 5.0)

# How much a token can be "extended" above MA99 (as a fraction, e.g. 0.15 =
# 15%) before it is considered overextended and loses scoring points.
MAX_EXTENSION_ABOVE_MA99 = _get_float("MAX_EXTENSION_ABOVE_MA99", 0.15)

# --- Funding rate thresholds -------------------------------------------
# Binance funding is decimal format: 0.0003 = 0.03%.
# IMPORTANT: funding sign is used as-is (never abs()'d) - a negative
# funding rate (shorts paying longs) is treated as a bullish signal for
# long setups, not just "small magnitude = healthy".
FUNDING_HEALTHY_THRESHOLD = _get_float("FUNDING_HEALTHY_THRESHOLD", 0.0003)   # <=  -> healthy
FUNDING_RISK_THRESHOLD = _get_float("FUNDING_RISK_THRESHOLD", 0.0005)         # >   -> risk warning / penalty

# --- Alert cooldown ---------------------------------------------------------
# Minimum time between two alerts for the SAME symbol.
ALERT_COOLDOWN_HOURS = _get_float("ALERT_COOLDOWN_HOURS", 24.0)

# --- Trend health / post-pullback continuation monitoring -------------------
# How often (hours) an already-alerted symbol is re-checked for pullback health.
TREND_HEALTH_CHECK_INTERVAL_HOURS = _get_float("TREND_HEALTH_CHECK_INTERVAL_HOURS", 4.0)
# How many days after the original alert to keep monitoring it.
TREND_HEALTH_MONITOR_WINDOW_DAYS = _get_float("TREND_HEALTH_MONITOR_WINDOW_DAYS", 3.0)
# Minimum pullback (in %, price dropping from the alert price) before the
# bot bothers evaluating "post-pullback" health at all.
MIN_PULLBACK_PCT_TO_EVALUATE = _get_float("MIN_PULLBACK_PCT_TO_EVALUATE", 2.0)
# trend_health_score (0-100) needed to send a "pullback healthy" Telegram update.
TREND_HEALTH_UPDATE_THRESHOLD = _get_int("TREND_HEALTH_UPDATE_THRESHOLD", 70)
# Minimum time between two "still healthy" update alerts for the same original alert.
TREND_HEALTH_UPDATE_COOLDOWN_HOURS = _get_float("TREND_HEALTH_UPDATE_COOLDOWN_HOURS", 12.0)

# --- Historical learning ----------------------------------------------------
# When enabled, the bot uses past alerts/outcomes stored in SQLite to give a
# small bonus to new setups that resemble past profitable patterns.
HISTORY_LEARNING_ENABLED = _get_int("HISTORY_LEARNING_ENABLED", 1) == 1
HISTORY_LEARNING_MIN_SCORE = _get_int("HISTORY_LEARNING_MIN_SCORE", 70)
HISTORY_LEARNING_MIN_MATCHES = _get_int("HISTORY_LEARNING_MIN_MATCHES", 3)
HISTORY_LEARNING_SCORE_TOLERANCE = _get_int("HISTORY_LEARNING_SCORE_TOLERANCE", 10)
HISTORY_LEARNING_MIN_SUCCESS_RATE = _get_float("HISTORY_LEARNING_MIN_SUCCESS_RATE", 0.6)
HISTORY_LEARNING_BONUS_MAX = _get_int("HISTORY_LEARNING_BONUS_MAX", 5)

# --- Result-tracking checkpoints (requirement 9) ----------------------------
# Offsets (in minutes) after an alert at which we record price performance.
OUTCOME_CHECKPOINTS_MINUTES = {
    "15m": 15,
    "1h": 60,
    "4h": 240,
    "24h": 1440,
}

# --- Database ---------------------------------------------------------------
DATABASE_PATH = os.getenv("DATABASE_PATH", "database/crypto_edge_bot.db")

# --- Networking ---------------------------------------------------------------
REQUEST_TIMEOUT_SECONDS = 10
REQUEST_MAX_RETRIES = 3
REQUEST_RETRY_BACKOFF_SECONDS = 1.5

# Small delay between per-symbol API calls to stay well within Binance's
# public rate limits.
SYMBOL_SCAN_DELAY_SECONDS = _get_float("SYMBOL_SCAN_DELAY_SECONDS", 0.3)

# --- Logging ------------------------------------------------------------------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")


def validate_config() -> list:
    """
    Returns a list of human-readable problems with the current configuration.
    An empty list means the config looks usable.
    """
    problems = []
    bot_token = (TELEGRAM_BOT_TOKEN or "").strip()
    chat_id = (TELEGRAM_CHAT_ID or "").strip()
    if not bot_token or bot_token.startswith("your_"):
        problems.append("TELEGRAM_BOT_TOKEN is not set (see .env.example).")
    if not chat_id or chat_id.startswith("your_"):
        problems.append("TELEGRAM_CHAT_ID is not set (see .env.example).")
    if FUNDING_HEALTHY_THRESHOLD >= FUNDING_RISK_THRESHOLD:
        problems.append(
            "FUNDING_HEALTHY_THRESHOLD should be lower than FUNDING_RISK_THRESHOLD."
        )
    return problems
