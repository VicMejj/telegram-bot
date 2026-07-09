"""
database/models.py
--------------------
SQL schema definitions for Crypto Edge Bot's SQLite database.

Tables:
  - market_snapshots        one row per symbol per scan (full indicator snapshot,
                             logged whether or not it triggered an alert)
  - open_interest_snapshots history of OI readings per symbol, used to compute
                             OI change between scans
  - alerts                  one row per Telegram alert that was sent
  - alert_outcomes          performance checkpoints (15m/1h/4h/24h) per alert
  - trend_health_updates    post-pullback continuation health checks per alert

reasons / risks / score_breakdown are stored as JSON-encoded TEXT columns
(see database/db.py, which json.dumps()/json.loads() them) - never as
plain freeform text.
"""

CREATE_MARKET_SNAPSHOTS_TABLE = """
CREATE TABLE IF NOT EXISTS market_snapshots (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol              TEXT NOT NULL,
    timestamp_utc       TEXT NOT NULL,
    timeframe           TEXT NOT NULL,
    price               REAL,
    ma7                 REAL,
    ma25                REAL,
    ma99                REAL,
    volume_ratio        REAL,
    funding_rate        REAL,
    open_interest       REAL,
    oi_change_pct       REAL,
    score               INTEGER
);
"""

CREATE_OPEN_INTEREST_SNAPSHOTS_TABLE = """
CREATE TABLE IF NOT EXISTS open_interest_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol          TEXT NOT NULL,
    open_interest   REAL NOT NULL,
    timestamp_utc   TEXT NOT NULL
);
"""

CREATE_ALERTS_TABLE = """
CREATE TABLE IF NOT EXISTS alerts (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol                      TEXT NOT NULL,
    timestamp_utc               TEXT NOT NULL,
    timeframe                   TEXT NOT NULL,
    price                       REAL,
    ma7                         REAL,
    ma25                        REAL,
    ma99                        REAL,
    volume_ratio                REAL,
    funding_rate                REAL,
    open_interest               REAL,
    open_interest_change_pct    REAL,
    score                       INTEGER,
    score_breakdown             TEXT,   -- JSON object
    reasons                     TEXT,   -- JSON array of strings
    risks                       TEXT,   -- JSON array of strings
    setup_type                  TEXT
);
"""

CREATE_ALERT_OUTCOMES_TABLE = """
CREATE TABLE IF NOT EXISTS alert_outcomes (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id            INTEGER NOT NULL REFERENCES alerts(id),
    symbol              TEXT NOT NULL,
    checkpoint          TEXT NOT NULL,     -- '15m' | '1h' | '4h' | '24h'
    alert_price         REAL,
    due_at              TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'pending',  -- 'pending' | 'recorded'
    price_at_checkpoint REAL,
    price_change_pct    REAL,
    max_gain_pct        REAL,
    max_drawdown_pct    REAL,
    hit_tp1             INTEGER,           -- 0/1 boolean, NULL = not evaluated
    hit_tp2             INTEGER,
    hit_tp3             INTEGER,
    hit_tp4             INTEGER,
    hit_stop            INTEGER,
    recorded_at         TEXT
);
"""

CREATE_TREND_HEALTH_UPDATES_TABLE = """
CREATE TABLE IF NOT EXISTS trend_health_updates (
    id                              INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id                        INTEGER NOT NULL REFERENCES alerts(id),
    symbol                          TEXT NOT NULL,
    timestamp_utc                   TEXT NOT NULL,
    pullback_depth_pct              REAL,
    oi_after_pullback               REAL,
    oi_change_after_pullback_pct    REAL,
    volume_after_pullback_ratio     REAL,
    price_holding_ma7               INTEGER,   -- 0/1 boolean
    price_holding_ma25              INTEGER,   -- 0/1 boolean
    funding_after_pullback          REAL,
    trend_health_score              INTEGER,
    continuation_status             TEXT,       -- 'healthy' | 'neutral' | 'weak'
    update_sent                     INTEGER NOT NULL DEFAULT 0  -- 0/1 boolean
);
"""

ALL_SCHEMAS = [
    CREATE_MARKET_SNAPSHOTS_TABLE,
    CREATE_OPEN_INTEREST_SNAPSHOTS_TABLE,
    CREATE_ALERTS_TABLE,
    CREATE_ALERT_OUTCOMES_TABLE,
    CREATE_TREND_HEALTH_UPDATES_TABLE,
]
