"""Logging setup of the platform API."""

import logging

from core.logs import json_handler


def configure_logging(level: int = logging.INFO) -> None:
    """Send the `merchant_platform` loggers to stderr as JSON lines."""
    logger = logging.getLogger("merchant_platform")
    logger.handlers[:] = [json_handler()]
    logger.setLevel(level)
    logger.propagate = False
