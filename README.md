# Crypto Edge Bot v0.1

A read-only market scanner for Binance USDT-M perpetual futures. It watches
all USDT perpetual pairs, computes a technical + funding + open-interest
score for each one from the last fully closed candle, sends a Telegram
alert on strong bullish setups, tracks how those setups perform afterward,
and follows up with an update if a pullback stays healthy.

**This version does NOT place trades.** It only:
- reads public Binance market data (candles, open interest, funding rate)
- calculates indicators, a score breakdown, reasons, and risks
- sends Telegram alerts (with a 24h per-symbol cooldown)
- saves everything to a local SQLite database
- tracks post-alert performance and post-pullback trend health

No Binance API key, no private/trading endpoints, no order execution, no
leverage/margin endpoints - ever.

---

## How it works

1. `main.py` loads every `TRADING` / `PERPETUAL` symbol quoted in USDT from
   Binance's futures exchange info.
2. For each symbol it:
   - fetches recent candles and **drops the currently-forming one**, so
     every calculation uses only the last fully closed candle (default
     timeframe `1h` - e.g. at 14:25 UTC it uses the candle that closed at
     14:00 UTC, not the one forming from 14:00-15:00)
   - computes MA7, MA25, MA99 (periods configurable via `FAST_MA`/`MID_MA`/`SLOW_MA`),
     current volume, average volume, and volume ratio
   - fetches the current funding rate (signed, never `abs()`'d)
   - fetches current open interest and compares it to the value saved from
     the previous scan (stored in SQLite)
   - scores the setup into a breakdown (see below), with human-readable
     `reasons` and `risks` lists
   - saves a row to `market_snapshots` regardless of score
3. If the score is `>= SCORE_THRESHOLD` (default 75) **and** the symbol
   isn't in its 24h alert cooldown, it sends a Telegram message and saves
   the alert (plus its score breakdown/reasons/risks) to SQLite, and
   schedules four future performance checkpoints (15m/1h/4h/24h).
4. Each loop, it resolves any performance checkpoints that have come due,
   and re-checks recently-alerted symbols for post-pullback trend health -
   sending a short Telegram update if a pullback looks structurally healthy.
5. It sleeps for `SCAN_INTERVAL_SECONDS` and repeats, forever, until you stop it.

Note: When `SCAN_INTERVAL_SECONDS` is set to the default `1800` (30 minutes),
the scanner synchronizes to the UTC :00 and :30 timestamps so scans occur at
those half-hour boundaries.

### Scoring

The score is built from six components (`score_breakdown`), summed and
clamped to 0-100:

| Component          | How it's earned                                              | Points   |
|---------------------|---------------------------------------------------------------|----------|
| `ma_trend_score`    | Price above MA99 (+20), MA7 above MA25 (+15)                  | up to 35 |
| `volume_score`      | Volume ratio above `VOLUME_RATIO_MIN` (default 2x)             | 20       |
| `oi_score`          | Open interest increased vs. last scan (by at least `OI_MIN_INCREASE_PCT`) | 20 |
| `funding_score`     | See funding logic below                                        | up to 20 |
| `structure_score`   | Token not overextended above MA99                              | 10       |
| `risk_penalty`      | See funding logic below                                        | -10 or 0 |

**Funding logic (decimal format, e.g. `0.0003` = `0.03%`) - the sign is
never discarded with `abs()`:**
- `funding <= FUNDING_HEALTHY_THRESHOLD` (default `0.0003`) → healthy, **+15**
- `funding < 0` (shorts paying longs) → extra bullish bonus, **+5** (on top
  of the healthy points, since negative funding is always `<=` the healthy
  threshold too)
- `funding > FUNDING_RISK_THRESHOLD` (default `0.0005`) → a risk warning is
  added to `risks` **and** a **-10** penalty is applied

`reasons` and `risks` are stored as **JSON arrays** in the database (e.g.
`["OI +12%", "Volume 3.1x average", "Funding negative"]`), never as a single
text blob.

---

## Project structure

```
crypto-edge-bot/
├── main.py                        # entry point / scan loop, cooldown, outcome + trend-health orchestration
├── config.py                      # loads .env; every threshold lives here, nothing hardcoded
├── logging_setup.py               # central logging configuration (console, UTC timestamps)
├── requirements.txt
├── .env.example                   # copy to .env and fill in
├── data/
│   ├── binance_client.py          # low-level Binance Futures API wrapper (public endpoints only)
│   ├── candles.py                 # fetch candles; get_closed_candles_df() drops the forming candle
│   ├── open_interest.py           # fetch OI + compute change vs open_interest_snapshots
│   └── funding.py                 # fetch current (signed) funding rate
├── strategy/
│   ├── indicators.py              # MA7/MA25/MA99, volume ratio, extension, MA-holding status
│   ├── scoring.py                 # score breakdown, reasons, risks, setup_type (no abs() on funding)
│   ├── ma_oi_funding_strategy.py  # orchestrates the full per-symbol evaluation pipeline
│   ├── outcome_tracker.py         # resolves due 15m/1h/4h/24h performance checkpoints
│   └── trend_health.py            # post-pullback continuation health scoring
├── alerts/
│   └── telegram.py                # formats + sends Telegram alerts and update messages
├── database/
│   ├── db.py                      # SQLite connection + all read/write helpers, cooldown logic
│   └── models.py                  # SQL table definitions (5 tables, see below)
└── README.md
```

---

## Database schema

| Table                     | Purpose                                                                 |
|----------------------------|--------------------------------------------------------------------------|
| `market_snapshots`         | One row per symbol per scan (every scan, alert or not)                  |
| `open_interest_snapshots`  | OI history per symbol, used to compute OI change between scans          |
| `alerts`                   | One row per Telegram alert sent, with JSON `score_breakdown`/`reasons`/`risks` |
| `alert_outcomes`           | 15m/1h/4h/24h performance checkpoints per alert                         |
| `trend_health_updates`     | Post-pullback continuation health checks per alert                     |

### `alert_outcomes` - result tracking (v0.1 status)

Four rows are scheduled automatically the moment an alert is saved. Each
scan loop resolves any that have come due:
- **`price_change_pct` is fully computed** - fetched current price vs. the
  alert-time price.
- **`max_gain_pct`, `max_drawdown_pct`, `hit_tp1`..`hit_tp4`, `hit_stop`
  are stub placeholders** (`NULL`/not computed) - the columns exist and are
  ready, but populating them properly needs continuous price sampling
  between the alert and the checkpoint plus defined TP/SL levels, which is
  out of scope for v0.1. See `strategy/outcome_tracker.py` for the exact
  status and how to extend it.

### `trend_health_updates` - post-pullback monitoring

After an alert, if price pulls back at least `MIN_PULLBACK_PCT_TO_EVALUATE`
from the alert price, the bot scores continuation health (0-100) from OI
holding up, volume staying active, and price holding MA7/MA25. If the score
clears `TREND_HEALTH_UPDATE_THRESHOLD` (default 70) and the symbol hasn't
already gotten an update recently, it sends a short Telegram follow-up, e.g.:

```
ALICE/USDT Update: Pullback healthy. OI +5.0%, volume 1.3x avg, price holding MA7/MA25. Pullback depth: 5.2%. Continuation score: 86/100.
```

---

## Setup

### 1. Requirements
- Python 3.11+
- A Telegram bot token and chat ID (see below)

### 2. Install dependencies

```bash
cd crypto-edge-bot
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Create a Telegram bot and get your chat ID

1. Open Telegram, message **@BotFather**, and run `/newbot`. Follow the
   prompts — you'll get a **bot token** like `123456789:AAExampleToken`.
2. Start a chat with your new bot, or add it to a group/channel.
3. Get your **chat ID**:
   - Send any message to the bot.
   - Visit `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser.
   - Look for `"chat":{"id": ...}` in the JSON response. For groups it will
     be a negative number.

### 4. Configure environment variables

```bash
cp .env.example .env
```

Edit `.env` and fill in `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` at
minimum. Every threshold (MAs, timeframe, score threshold, funding levels,
cooldown hours, trend-health settings) is adjustable there - see the
comments in `.env.example`.

### 5. Run it

```bash
python3 main.py
```

Console output looks like:

```
2026-07-06 20:00:00 UTC | INFO    | __main__ | Crypto Edge Bot v0.1 starting up...
2026-07-06 20:00:00 UTC | INFO    | __main__ | Initializing database...
2026-07-06 20:00:01 UTC | INFO    | __main__ | Loading USDT perpetual futures symbols...
2026-07-06 20:00:02 UTC | INFO    | __main__ | Symbols loaded: 312. Timeframe=1h, FAST_MA=7, MID_MA=25, SLOW_MA=99, Score threshold=75, Cooldown=24.0h, Scan interval=1800s
2026-07-06 20:00:02 UTC | INFO    | __main__ | ============================================================
2026-07-06 20:00:02 UTC | INFO    | __main__ | Starting scan...
2026-07-06 20:04:11 UTC | INFO    | __main__ | ALICEUSDT: ALERT sent (score=82, alert_id=1) - database saved
2026-07-06 20:05:00 UTC | INFO    | __main__ | Scan complete. 1/312 symbols met the score threshold this pass.
```

Alerts land in your Telegram chat like:

```
🚨 ALICE/USDT — Score 82/100
Setup: MA Trend + OI Rising + Volume Surge + Healthy Funding + Structure Intact
Timeframe: 1h
Timestamp (UTC): 2026-07-06T20:04:11+00:00

Price: 1.4979
MA7: above | MA25: above | MA99: above
Volume Ratio: 3.5x
OI Change: +11.1%
Funding: -0.020%

Score Breakdown:
  ma_trend_score: +35
  volume_score: +20
  oi_score: +20
  funding_score: +20
  structure_score: +10
  risk_penalty: +0
  final_score: +100

Reasons:
✅ Price above MA99 (1.49788 > 1.33598)
✅ MA7 above MA25 (short-term trend bullish)
✅ Volume 3.5x average
✅ OI +11.1%
✅ Funding healthy (-0.020%)
✅ Funding negative (shorts paying longs)
✅ Price not overextended above MA99

Risks:
None flagged
```

### 6. Inspect saved data

```bash
sqlite3 database/crypto_edge_bot.db "SELECT timestamp_utc, symbol, score, setup_type FROM alerts ORDER BY id DESC LIMIT 20;"
sqlite3 database/crypto_edge_bot.db "SELECT * FROM alert_outcomes ORDER BY id DESC LIMIT 20;"
sqlite3 database/crypto_edge_bot.db "SELECT * FROM trend_health_updates ORDER BY id DESC LIMIT 20;"
```

---

## Testing with fewer symbols

While tuning settings, set `MAX_SYMBOLS` in `.env` to something small (e.g.
`20`) so a full scan finishes quickly. Set `LOG_LEVEL=DEBUG` to see
per-symbol scores and skip reasons on every scan.

## Rate limits

`SYMBOL_SCAN_DELAY_SECONDS` adds a small delay between each symbol's API
calls, and `binance_client.py` retries failed requests with backoff. If you
scan very frequently or track many symbols, consider raising
`SCAN_INTERVAL_SECONDS`.

## Roadmap ideas (not included in v0.1)

- Full `max_gain_pct` / `max_drawdown_pct` / TP-hit tracking (needs
  continuous price sampling between alert and checkpoint, plus defined
  TP/SL levels - the `alert_outcomes` columns already exist for this)
- Bearish/short setups
- Multi-timeframe confirmation
- Backtesting against historical data
- A dashboard/web UI for alert history
- Actual order execution (a future, clearly-separated version - this one
  intentionally never touches trading, margin, or leverage endpoints)
#   t e l e g r a m - b o t  
 