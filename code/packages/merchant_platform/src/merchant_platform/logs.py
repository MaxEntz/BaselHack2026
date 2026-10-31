"""Logging setup of the platform API."""

import logging
import re

from core.logs import json_handler

# The secret link's token (`GET /login?token=`, #132) must never reach a log line.
TOKEN = re.compile(r"(token=)[^&\s\"]+")


class RedactTokens(logging.Filter):
    """Masks `token=` query values in a record's arguments: uvicorn's access log has the URL."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(
                TOKEN.sub(r"\1***", arg) if isinstance(arg, str) else arg for arg in record.args
            )
        if isinstance(record.msg, str):
            record.msg = TOKEN.sub(r"\1***", record.msg)
        return True


def configure_logging(level: int = logging.INFO) -> None:
    """Send every logger to stderr as JSON lines, as the gateway and the node do (§12).

    The handler sits on the root logger, so the builder's and the agent's lines (`build_agent`,
    `builder_cache`, `reservation cancelled by merchant`) come out too. Uvicorn's loggers keep
    the handlers of its own config and don't propagate, so its access log stays plain text, with
    the secret link's token masked.
    """
    root = logging.getLogger()
    root.handlers[:] = [json_handler()]
    root.setLevel(level)
    # One INFO line per OpenAI request would drown the platform's own lines.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, RedactTokens) for f in access.filters):
        access.addFilter(RedactTokens())
