"""
strategy/history_learning.py
----------------------------
Lightweight history-based scoring layer.

The bot reads its own historical alerts and outcome checkpoints from SQLite,
then gives a small confidence bonus to a new setup when it looks similar to
past alerts that were followed by positive price movement.

This is intentionally simple and transparent: it is not a full ML system,
but it gives the bot a basic "learn from history" capability using the data
it already stores.
"""

from __future__ import annotations

import config


def _normalize_setup_type(value: str | None) -> str:
    return (value or "").strip()


def _build_context_matches(current_setup_type: str, current_symbol: str | None, current_timeframe: str | None, history_rows: list[dict]) -> list[dict]:
    matches = []
    for row in history_rows or []:
        setup_type = _normalize_setup_type(row.get("setup_type"))
        if setup_type != current_setup_type:
            continue

        symbol = (row.get("symbol") or "").upper()
        timeframe = (row.get("timeframe") or "").strip()

        symbol_ok = not current_symbol or not symbol or symbol == current_symbol.upper()
        timeframe_ok = not current_timeframe or not timeframe or timeframe == current_timeframe
        if not (symbol_ok and timeframe_ok):
            continue

        score = row.get("score")
        if score is None:
            continue
        matches.append(row)
    return matches


def build_history_adjustment(current_score: int, current_setup_type: str, history_rows: list[dict], current_symbol: str | None = None, current_timeframe: str | None = None) -> dict:
    """
    Returns a dict like:
      {"bonus_points": 0, "reason": ""}

    The adjustment is only applied when there are enough similar historical
    alerts and the historical win rate is strong enough.
    """
    if not config.HISTORY_LEARNING_ENABLED:
        return {"bonus_points": 0, "reason": ""}

    if current_score < config.HISTORY_LEARNING_MIN_SCORE:
        return {"bonus_points": 0, "reason": ""}

    if not current_setup_type:
        return {"bonus_points": 0, "reason": ""}

    similar = _build_context_matches(current_setup_type, current_symbol, current_timeframe, history_rows)
    if len(similar) < config.HISTORY_LEARNING_MIN_MATCHES:
        return {"bonus_points": 0, "reason": ""}

    wins = sum(1 for row in similar if bool(row.get("success")))
    success_rate = wins / len(similar)

    if success_rate < config.HISTORY_LEARNING_MIN_SUCCESS_RATE:
        return {"bonus_points": 0, "reason": ""}

    bonus_points = int(round(success_rate * config.HISTORY_LEARNING_BONUS_MAX))
    bonus_points = max(1, min(config.HISTORY_LEARNING_BONUS_MAX, bonus_points))

    context_bits = []
    if current_symbol:
        context_bits.append(f"symbol {current_symbol}")
    if current_timeframe:
        context_bits.append(f"timeframe {current_timeframe}")
    context_text = f" for {' and '.join(context_bits)}" if context_bits else ""

    reason = (
        f"History boost{context_text}: {wins}/{len(similar)} similar alerts with this setup were profitable "
        f"({success_rate:.0%} success rate)."
    )

    return {"bonus_points": bonus_points, "reason": reason}


def apply_history_adjustment(base_score: int, setup_type: str, history_rows: list[dict], current_symbol: str | None = None, current_timeframe: str | None = None) -> tuple[int, str]:
    """Adjusts a base score using historical alert performance and returns (adjusted_score, reason)."""
    adjustment = build_history_adjustment(base_score, setup_type, history_rows, current_symbol=current_symbol, current_timeframe=current_timeframe)
    if adjustment["bonus_points"] <= 0:
        return base_score, ""

    adjusted_score = max(0, min(100, base_score + adjustment["bonus_points"]))
    return adjusted_score, adjustment["reason"]
