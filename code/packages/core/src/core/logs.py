"""JSON log lines: one object per record, with the `extra` fields as top-level keys."""

import json
import logging
from datetime import UTC, datetime
from typing import Any

# Attributes every LogRecord has; anything else came from `extra=`, except uvicorn's ANSI copy.
_STANDARD = set(vars(logging.makeLogRecord({}))) | {
    "message",
    "asctime",
    "taskName",
    "color_message",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "event": record.getMessage(),
        }
        entry.update({k: v for k, v in vars(record).items() if k not in _STANDARD})
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def json_handler() -> logging.Handler:
    """A stderr handler writing JSON lines."""
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    return handler


def use_json_logs(level: int = logging.INFO) -> None:
    """Send every logger to stderr as JSON lines from `level` up: the setup of each process
    (gateway, BATP node, platform API), so their lines read the same (AGENTS.md §12)."""
    root = logging.getLogger()
    root.handlers[:] = [json_handler()]
    root.setLevel(level)
