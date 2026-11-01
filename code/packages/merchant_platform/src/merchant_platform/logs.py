"""Logging setup of the platform API."""

import hashlib
import logging
import re
import traceback

from core.logs import json_handler

# The secret link's token (`GET /login?token=`) and a chat session's id (`GET /session?
# session_id=`), which acts for its merchant, must never reach a log line (#132): a session
# that just built its merchant claims the owner cookie with its id alone (`/session/claim`).
# Lines name a session by `session_tag` instead, and a turn's traceback goes through
# `traceback_without`.
TOKEN = re.compile(r"((?:token|session_id)=)[^&\s\"]+")
SESSION_TAG_CHARS = 8


def session_tag(session_id: str) -> str:
    """A chat session's name in the logs: the start of its id's SHA-256, which tells one
    session's lines from another's but can't be turned back into the id (#220)."""
    return hashlib.sha256(session_id.encode()).hexdigest()[:SESSION_TAG_CHARS]


def traceback_without(error: BaseException, session_id: str) -> str:
    """`error`'s traceback as `log.exception` writes it, with the session's id replaced by its
    tag: an exception's message may hold the id (a `KeyError` on it, for one)."""
    text = "".join(traceback.format_exception(error)).rstrip("\n")
    return text.replace(session_id, session_tag(session_id))


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
