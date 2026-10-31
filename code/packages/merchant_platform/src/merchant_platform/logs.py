"""Logging setup of the platform API."""

import logging

from core.logs import json_handler


def configure_logging(level: int = logging.INFO) -> None:
    """Send every logger to stderr as JSON lines, as the gateway and the node do (§12).

    The handler sits on the root logger, so the builder's and the agent's lines (`build_agent`,
    `builder_cache`, `reservation cancelled by merchant`) come out too. Uvicorn's loggers keep
    the handlers of its own config and don't propagate, so its access log stays plain text.
    """
    root = logging.getLogger()
    root.handlers[:] = [json_handler()]
    root.setLevel(level)
    # One INFO line per OpenAI request would drown the platform's own lines.
    logging.getLogger("httpx").setLevel(logging.WARNING)
