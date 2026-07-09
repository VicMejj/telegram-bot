"""
strategy/scoring.py
--------------------
Turns raw indicators + funding + open-interest data into:
  - a score breakdown (oi_score, volume_score, funding_score, ma_trend_score,
    structure_score, risk_penalty, final_score)
  - a list of `reasons` (why the setup looks good), as a plain Python list
    of strings (stored as JSON in the database, never as one blob of text)
  - a list of `risks` (things to be careful about), also a plain list
  - a human-readable `setup_type` label

Funding logic (IMPORTANT - do not take abs() of funding):
  - funding <= FUNDING_HEALTHY_THRESHOLD (0.0003 by default)  -> healthy, +15
  - funding < 0                                               -> extra bullish, +5 bonus
  - funding > FUNDING_RISK_THRESHOLD (0.0005 by default)      -> risk warning AND -10 penalty
Since a negative rate is always <= the healthy threshold, negative funding
always scores both the healthy points AND the bonus.

Point weights:
  ma_trend_score  : price > MA99 (+20) and MA7 > MA25 (+15)   -> max 35
  volume_score    : volume ratio > VOLUME_RATIO_MIN            -> max 20
  oi_score        : OI increased by >= OI_MIN_INCREASE_PCT     -> max 20
  funding_score   : healthy (+15) and/or negative bonus (+5)   -> max 20
  structure_score : token not overextended above MA99          -> max 10
  risk_penalty    : funding above risk threshold               -> -10 (else 0)

  final_score = clamp(sum of the above, 0, 100)
"""

import config
from strategy.history_learning import apply_history_adjustment

MIN_SCORE = 0
MAX_SCORE = 100


def passes_hard_filters(indicators: dict, funding_rate: float | None, oi_info: dict) -> tuple[bool, list[str]]:
    """Applies the hard filters the client described before scoring begins."""
    reasons: list[str] = []

    current_price = indicators.get("current_price")
    ma7 = indicators.get("ma7")
    ma25 = indicators.get("ma25")
    ma99 = indicators.get("ma99")
    volume_ratio = indicators.get("volume_ratio")

    if current_price is None or ma7 is None or ma25 is None or ma99 is None:
        reasons.append("Missing MA/price data")
        return False, reasons

    if not (current_price > ma7):
        reasons.append("Price must be above MA7")
    if not (ma7 > ma25):
        reasons.append("MA7 must be above MA25")
    if not (ma25 > ma99):
        reasons.append("MA25 must be above MA99")

    if volume_ratio is None or volume_ratio < config.VOLUME_RATIO_MIN:
        reasons.append(f"Volume below threshold (need >= {config.VOLUME_RATIO_MIN:.1f}x)")

    oi_15m = oi_info.get("oi_15m_change_pct")
    oi_30m = oi_info.get("oi_30m_change_pct")
    oi_1h = oi_info.get("oi_1h_change_pct")

    if oi_15m is None or oi_15m < config.OI_15M_MIN_INCREASE_PCT:
        reasons.append(f"OI15m below threshold (need >= {config.OI_15M_MIN_INCREASE_PCT:.1f}%)")
    if oi_30m is None or oi_30m < config.OI_30M_MIN_INCREASE_PCT:
        reasons.append(f"OI30m below threshold (need >= {config.OI_30M_MIN_INCREASE_PCT:.1f}%)")
    if oi_1h is None or oi_1h < config.OI_1H_MIN_INCREASE_PCT:
        reasons.append(f"OI1H below threshold (need >= {config.OI_1H_MIN_INCREASE_PCT:.1f}%)")

    if oi_15m is not None and oi_30m is not None and oi_30m <= oi_15m:
        reasons.append("OI30m must be stronger than OI15m")
    if oi_30m is not None and oi_1h is not None and oi_1h <= oi_30m:
        reasons.append("OI1H must be stronger than OI30m")

    if funding_rate is None or funding_rate > config.FUNDING_HEALTHY_THRESHOLD:
        reasons.append("Funding is not healthy")

    return not reasons, reasons


def _score_ma_trend(indicators: dict, reasons: list, risks: list) -> int:
    price = indicators.get("current_price")
    ma25 = indicators.get("ma25")
    ma99 = indicators.get("ma99")
    ma7 = indicators.get("ma7")

    points = 0
    if ma7 is not None and ma25 is not None and ma99 is not None and price is not None:
        if price > ma7 and ma7 > ma25 and ma25 > ma99:
            points += 35
            reasons.append(f"Price above MA7 and MA stack bullish ({price:.6g} > {ma7:.6g} > {ma25:.6g} > {ma99:.6g})")
        else:
            reasons.append("MA alignment is not fully bullish")
    return points


def _score_volume(indicators: dict, reasons: list, risks: list) -> int:
    volume_ratio = indicators.get("volume_ratio")
    if volume_ratio is not None and volume_ratio >= config.VOLUME_RATIO_MIN:
        reasons.append(f"Volume {volume_ratio:.1f}x average")
        return 20
    if volume_ratio is not None:
        risks.append(f"Volume only {volume_ratio:.1f}x average (no surge)")
    return 0


def _score_open_interest(oi_info: dict, reasons: list, risks: list) -> int:
    oi_15m = oi_info.get("oi_15m_change_pct")
    oi_30m = oi_info.get("oi_30m_change_pct")
    oi_1h = oi_info.get("oi_1h_change_pct")

    if (
        oi_15m is not None
        and oi_30m is not None
        and oi_1h is not None
        and oi_15m >= config.OI_15M_MIN_INCREASE_PCT
        and oi_30m >= config.OI_30M_MIN_INCREASE_PCT
        and oi_1h >= config.OI_1H_MIN_INCREASE_PCT
        and oi_30m > oi_15m
        and oi_1h > oi_30m
    ):
        reasons.append(
            f"OI trend bullish ({oi_15m:.1f}% / {oi_30m:.1f}% / {oi_1h:.1f}%)"
        )
        return 20

    if oi_1h is not None:
        risks.append(f"OI trend weak ({oi_15m if oi_15m is not None else 'n/a'}% / {oi_30m if oi_30m is not None else 'n/a'}% / {oi_1h if oi_1h is not None else 'n/a'}%)")
    return 0


def _score_funding(funding_rate: float | None, reasons: list, risks: list) -> tuple[int, int]:
    """
    Returns (funding_score, risk_penalty) as a tuple, since funding affects
    both a positive score component and a separate risk-penalty component.
    """
    funding_score = 0
    risk_penalty = 0

    if funding_rate is None:
        return funding_score, risk_penalty

    # Healthy: funding <= threshold (sign preserved - never abs()'d).
    if funding_rate <= config.FUNDING_HEALTHY_THRESHOLD:
        funding_score += 15
        reasons.append(f"Funding healthy ({funding_rate * 100:.3f}%)")

    # Extra bullish bonus for negative funding (shorts paying longs).
    if funding_rate < 0:
        funding_score += 5
        reasons.append("Funding negative (shorts paying longs)")

    # Risk warning + penalty for elevated funding.
    if funding_rate > config.FUNDING_RISK_THRESHOLD:
        risk_penalty -= 10
        risks.append(
            f"Funding elevated ({funding_rate * 100:.3f}%) - risk of long squeeze / crowded longs"
        )

    return funding_score, risk_penalty


def _score_structure(indicators: dict, reasons: list, risks: list) -> int:
    is_overextended = indicators.get("is_overextended", False)
    extension_pct = indicators.get("extension_pct")

    if not is_overextended:
        reasons.append("Price not overextended above MA99")
        return 10

    if extension_pct is not None:
        risks.append(f"Price extended {extension_pct * 100:.1f}% above MA99 - check before entering")
    return 0


def _build_setup_type(ma_trend_score: int, volume_score: int, oi_score: int, funding_score: int, structure_score: int) -> str:
    """Builds a short human-readable label describing which components fired."""
    parts = []
    if ma_trend_score > 0:
        parts.append("MA Trend")
    if oi_score > 0:
        parts.append("OI Rising")
    if volume_score > 0:
        parts.append("Volume Surge")
    if funding_score > 0:
        parts.append("Healthy Funding")
    if structure_score > 0:
        parts.append("Structure Intact")
    return " + ".join(parts) if parts else "Weak / No Setup"


def score_setup(indicators: dict, funding_rate: float | None, oi_info: dict, history_rows: list[dict] | None = None) -> dict:
    """
    Takes:
      indicators: dict from strategy.indicators.build_indicator_snapshot()
      funding_rate: float (signed decimal, e.g. 0.0003 = 0.03%), never abs()'d
      oi_info: dict from data.open_interest.get_open_interest_change()

    Returns:
      {
        "final_score": int,
        "score_breakdown": {
            "ma_trend_score": int, "volume_score": int, "oi_score": int,
            "funding_score": int, "structure_score": int, "risk_penalty": int,
            "final_score": int,
        },
        "reasons": list[str],
        "risks": list[str],
        "setup_type": str,
      }
    """
    reasons: list = []
    risks: list = []

    ma_trend_score = _score_ma_trend(indicators, reasons, risks)
    volume_score = _score_volume(indicators, reasons, risks)
    oi_score = _score_open_interest(oi_info, reasons, risks)
    funding_score, risk_penalty = _score_funding(funding_rate, reasons, risks)
    structure_score = _score_structure(indicators, reasons, risks)

    raw_total = ma_trend_score + volume_score + oi_score + funding_score + structure_score + risk_penalty
    final_score = max(MIN_SCORE, min(MAX_SCORE, raw_total))

    setup_type = _build_setup_type(ma_trend_score, volume_score, oi_score, funding_score, structure_score)
    if history_rows is None:
        history_rows = []

    adjusted_score, history_reason = apply_history_adjustment(
        final_score,
        setup_type,
        history_rows,
        current_symbol=indicators.get("symbol"),
        current_timeframe=config.TIMEFRAME,
    )
    if history_reason:
        reasons.append(history_reason)

    final_score = adjusted_score

    score_breakdown = {
        "ma_trend_score": ma_trend_score,
        "volume_score": volume_score,
        "oi_score": oi_score,
        "funding_score": funding_score,
        "structure_score": structure_score,
        "risk_penalty": risk_penalty,
        "final_score": final_score,
    }

    return {
        "final_score": final_score,
        "score_breakdown": score_breakdown,
        "reasons": reasons,
        "risks": risks,
        "setup_type": setup_type,
    }
