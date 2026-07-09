"""
data/funding.py
----------------
Fetches the current (most recently applied) funding rate for a USDT-M
perpetual futures symbol.

Funding is returned as a signed decimal fraction (Binance format), e.g.
0.0003 = 0.03%, -0.0001 = -0.01%. Callers must NOT take abs() of this value:
the sign matters for scoring (negative funding = shorts paying longs, which
is bullish for long setups).
"""

import logging

from data.binance_client import BinanceClient

logger = logging.getLogger(__name__)


def get_funding_rate(client: BinanceClient, symbol: str) -> float:
    """
    Returns the last funding rate as a signed decimal fraction.
    """
    info = client.get_premium_index(symbol)
    rate = float(info["lastFundingRate"])
    logger.debug("%s: funding fetched (%.5f)", symbol, rate)
    return rate
