"""`core.logs.use_json_logs`: the one log setup of the gateway, the node and the platform (#220)."""

import io
import json
import logging
from collections.abc import Iterator

import pytest

from core.logs import JsonFormatter, use_json_logs


@pytest.fixture
def root() -> Iterator[logging.Logger]:
    """The root logger, put back as it was for the other tests."""
    logger = logging.getLogger()
    handlers, level = logger.handlers[:], logger.level
    yield logger
    logger.handlers[:] = handlers
    logger.setLevel(level)


def test_every_logger_writes_json_lines_from_the_level_up(root: logging.Logger) -> None:
    root.addHandler(logging.NullHandler())  # whatever was set before is replaced

    use_json_logs(logging.WARNING)

    (handler,) = root.handlers
    assert isinstance(handler, logging.StreamHandler)
    assert isinstance(handler.formatter, JsonFormatter)
    buffer = io.StringIO()
    handler.setStream(buffer)
    logging.getLogger("gateway.tools").info("below the level")
    logging.getLogger("merchant_node").warning("kept", extra={"merchant": "le-petit-zinc"})
    [line] = [json.loads(text) for text in buffer.getvalue().splitlines()]
    assert (line["logger"], line["level"], line["event"], line["merchant"]) == (
        "merchant_node",
        "warning",
        "kept",
        "le-petit-zinc",
    )


def test_the_default_level_is_info_and_a_second_call_keeps_one_handler(
    root: logging.Logger,
) -> None:
    use_json_logs()
    use_json_logs()

    assert root.level == logging.INFO
    assert len(root.handlers) == 1
