"""
database/db.py
----------------
SQLite persistence layer for Crypto Edge Bot.

Responsibilities:
  - Create the database file/tables if they don't exist yet (init_db)
  - Save market snapshots (every scan, whether or not it alerts)
  - Save/read open-interest snapshots used to compute OI change over time
  - Enforce the 24h alert cooldown per symbol
  - Save alerts (with JSON-encoded score breakdown / reasons / risks) and
    schedule their future outcome-tracking checkpoints
  - Resolve due outcome checkpoints
  - Track post-pullback trend-health checks and whether an update alert
    was already sent for a given alert

Uses the standard library's sqlite3 module - no ORM needed for a project
this size. Rows are returned as dict-like sqlite3.Row objects/plain dicts
so callers can use ["column_name"] access.
"""

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import config
from database.models import ALL_SCHEMAS


def _ensure_parent_dir(path: str) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def get_connection():
    """Yields a sqlite3 connection (with Row factory), committing on success
    and closing always."""
    _ensure_parent_dir(config.DATABASE_PATH)
    conn = sqlite3.connect(config.DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    """Creates the database file and all required tables if they don't exist."""
    with get_connection() as conn:
        cursor = conn.cursor()
        for schema in ALL_SCHEMAS:
            cursor.execute(schema)


# ---------------------------------------------------------------------------
# Market snapshots (logged every scan, alert or not)
# ---------------------------------------------------------------------------

def save_market_snapshot(evaluation: dict) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO market_snapshots (
                symbol, timestamp_utc, timeframe, price, ma7, ma25, ma99,
                volume_ratio, funding_rate, open_interest, oi_change_pct, score
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                evaluation["symbol"],
                _now_iso(),
                evaluation["timeframe"],
                evaluation["price"],
                evaluation["ma7"],
                evaluation["ma25"],
                evaluation["ma99"],
                evaluation["volume_ratio"],
                evaluation["funding_rate"],
                evaluation["open_interest"],
                evaluation["oi_change_pct"],
                evaluation["score"],
            ),
        )


# ---------------------------------------------------------------------------
# Open interest snapshots
# ---------------------------------------------------------------------------

def get_last_open_interest(symbol: str) -> float | None:
    """Returns the most recently stored OI value for `symbol`, or None."""
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT open_interest FROM open_interest_snapshots
            WHERE symbol = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (symbol,),
        ).fetchone()
        return float(row["open_interest"]) if row else None


def save_open_interest_snapshot(symbol: str, open_interest: float) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO open_interest_snapshots (symbol, open_interest, timestamp_utc)
            VALUES (?, ?, ?)
            """,
            (symbol, open_interest, _now_iso()),
        )


# ---------------------------------------------------------------------------
# Alerts + cooldown
# ---------------------------------------------------------------------------

def is_symbol_in_cooldown(symbol: str) -> bool:
    """
    Returns True if `symbol` had an alert sent within the last
    ALERT_COOLDOWN_HOURS hours (i.e. a new alert should NOT be sent yet).
    """
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT timestamp_utc FROM alerts
            WHERE symbol = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (symbol,),
        ).fetchone()

    if row is None:
        return False

    last_alert_time = datetime.fromisoformat(row["timestamp_utc"])
    cooldown_cutoff = datetime.now(timezone.utc) - timedelta(hours=config.ALERT_COOLDOWN_HOURS)
    return last_alert_time > cooldown_cutoff


def save_alert(evaluation: dict) -> int:
    """
    Persists one alert row and schedules its future outcome-tracking
    checkpoints (15m/1h/4h/24h) in `alert_outcomes`.

    `evaluation` is the dict returned by
    strategy.ma_oi_funding_strategy.evaluate_symbol(), plus score_breakdown/
    reasons/risks/setup_type from strategy.scoring.score_setup().

    Returns the new alert's id.
    """
    timestamp_utc = _now_iso()

    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO alerts (
                symbol, timestamp_utc, timeframe, price, ma7, ma25, ma99,
                volume_ratio, funding_rate, open_interest, open_interest_change_pct,
                score, score_breakdown, reasons, risks, setup_type
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                evaluation["symbol"],
                timestamp_utc,
                evaluation["timeframe"],
                evaluation["price"],
                evaluation["ma7"],
                evaluation["ma25"],
                evaluation["ma99"],
                evaluation["volume_ratio"],
                evaluation["funding_rate"],
                evaluation["open_interest"],
                evaluation["oi_change_pct"],
                evaluation["score"],
                json.dumps(evaluation.get("score_breakdown", {})),
                json.dumps(evaluation.get("reasons", [])),
                json.dumps(evaluation.get("risks", [])),
                evaluation.get("setup_type", ""),
            ),
        )
        alert_id = cursor.lastrowid

        # Schedule outcome-tracking checkpoints (requirement 9).
        alert_dt = datetime.fromisoformat(timestamp_utc)
        for checkpoint, minutes in config.OUTCOME_CHECKPOINTS_MINUTES.items():
            due_at = (alert_dt + timedelta(minutes=minutes)).isoformat()
            conn.execute(
                """
                INSERT INTO alert_outcomes (
                    alert_id, symbol, checkpoint, alert_price, due_at, status
                ) VALUES (?, ?, ?, ?, ?, 'pending')
                """,
                (alert_id, evaluation["symbol"], checkpoint, evaluation["price"], due_at),
            )

    return alert_id


# ---------------------------------------------------------------------------
# Outcome tracking (requirement 9)
# ---------------------------------------------------------------------------

def get_due_outcomes() -> list:
    """
    Returns pending alert_outcomes rows whose due_at has passed, as a list
    of plain dicts: {id, alert_id, symbol, checkpoint, alert_price, due_at}.
    """
    now_iso = _now_iso()
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, alert_id, symbol, checkpoint, alert_price, due_at
            FROM alert_outcomes
            WHERE status = 'pending' AND due_at <= ?
            """,
            (now_iso,),
        ).fetchall()
    return [dict(row) for row in rows]


def record_alert_outcome(outcome_id: int, price_at_checkpoint: float, price_change_pct: float | None) -> None:
    """
    Records the resolved price/performance for a due outcome checkpoint.

    NOTE: max_gain_pct, max_drawdown_pct, hit_tp1..hit_tp4, hit_stop are not
    populated here (see strategy/outcome_tracker.py docstring) - the columns
    exist for future logic that tracks price continuously between the
    alert and the checkpoint.
    """
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE alert_outcomes
            SET price_at_checkpoint = ?,
                price_change_pct = ?,
                status = 'recorded',
                recorded_at = ?
            WHERE id = ?
            """,
            (price_at_checkpoint, price_change_pct, _now_iso(), outcome_id),
        )


# ---------------------------------------------------------------------------
# Trend health / post-pullback monitoring (requirement 10/11)
# ---------------------------------------------------------------------------

def get_alerts_needing_health_check() -> list:
    """
    Returns alerts (as plain dicts) that are:
      - within TREND_HEALTH_MONITOR_WINDOW_DAYS of their original alert time
      - not checked within the last TREND_HEALTH_CHECK_INTERVAL_HOURS

    Each dict includes: id, symbol, price, open_interest, timestamp_utc.
    """
    window_cutoff = (
        datetime.now(timezone.utc) - timedelta(days=config.TREND_HEALTH_MONITOR_WINDOW_DAYS)
    ).isoformat()
    recheck_cutoff = (
        datetime.now(timezone.utc) - timedelta(hours=config.TREND_HEALTH_CHECK_INTERVAL_HOURS)
    ).isoformat()

    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT a.id, a.symbol, a.price, a.open_interest, a.timestamp_utc
            FROM alerts a
            WHERE a.timestamp_utc >= ?
              AND a.id NOT IN (
                  SELECT alert_id FROM trend_health_updates
                  WHERE timestamp_utc >= ?
              )
            """,
            (window_cutoff, recheck_cutoff),
        ).fetchall()
    return [dict(row) for row in rows]


def get_recent_alert_history_rows(limit: int = 200) -> list[dict]:
    """
    Returns a compact history view of past alerts and their outcomes so the
    scoring logic can learn from earlier patterns.
    """
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                a.setup_type,
                a.score,
                CASE
                    WHEN SUM(CASE WHEN ao.price_change_pct IS NOT NULL AND ao.price_change_pct > 0 THEN 1 ELSE 0 END) > 0 THEN 1 ELSE 0
                END AS success
            FROM alerts a
            LEFT JOIN alert_outcomes ao ON ao.alert_id = a.id
            GROUP BY a.id
            ORDER BY a.timestamp_utc DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def save_trend_health_update(alert_id: int, symbol: str, health: dict) -> int:
    """Persists one trend_health_updates row. Returns its id."""
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO trend_health_updates (
                alert_id, symbol, timestamp_utc, pullback_depth_pct,
                oi_after_pullback, oi_change_after_pullback_pct,
                volume_after_pullback_ratio, price_holding_ma7, price_holding_ma25,
                funding_after_pullback, trend_health_score, continuation_status,
                update_sent
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                alert_id,
                symbol,
                _now_iso(),
                health["pullback_depth_pct"],
                health["oi_after_pullback"],
                health["oi_change_after_pullback_pct"],
                health["volume_after_pullback_ratio"],
                int(bool(health["price_holding_ma7"])) if health["price_holding_ma7"] is not None else None,
                int(bool(health["price_holding_ma25"])) if health["price_holding_ma25"] is not None else None,
                health["funding_after_pullback"],
                health["trend_health_score"],
                health["continuation_status"],
            ),
        )
        return cursor.lastrowid


def get_last_trend_health_update(alert_id: int) -> dict | None:
    """
    Returns the most recent trend_health_updates row for `alert_id` as a
    plain dict, or None if no update exists.
    """
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT * FROM trend_health_updates
            WHERE alert_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (alert_id,),
        ).fetchone()
    return dict(row) if row else None


def was_update_alert_recently_sent(alert_id: int) -> bool:
    """
    Returns True if a trend-health "still healthy" update alert was already
    sent for this alert_id within TREND_HEALTH_UPDATE_COOLDOWN_HOURS.
    """
    cutoff = (
        datetime.now(timezone.utc) - timedelta(hours=config.TREND_HEALTH_UPDATE_COOLDOWN_HOURS)
    ).isoformat()
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT id FROM trend_health_updates
            WHERE alert_id = ? AND update_sent = 1 AND timestamp_utc >= ?
            LIMIT 1
            """,
            (alert_id, cutoff),
        ).fetchone()
    return row is not None


def mark_update_alert_sent(trend_health_update_id: int) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE trend_health_updates SET update_sent = 1 WHERE id = ?",
            (trend_health_update_id,),
        )
