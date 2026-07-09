"""
logging_setup.py
------------------
Configures a single, consistent logging format for the whole bot.

Every module logs through `logging.getLogger(__name__)`. Call
`configure_logging()` once, from main.py, before anything else runs.
"""

import logging
import time

import config


def configure_logging() -> None:
    level = getattr(logging, config.LOG_LEVEL.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s UTC | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # Make timestamps genuinely UTC (basicConfig defaults to local time).
    logging.Formatter.converter = time.gmtime
    # Silence noisy third-party libraries a little.
    logging.getLogger("urllib3").setLevel(logging.WARNING)
