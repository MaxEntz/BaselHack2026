"""The BATP Internet-Draft's JSON Schemas (#63) are generated from `core.models`."""

import batp_schemas
import pytest


def test_committed_schemas_match_core_models() -> None:
    # Fails when a contract model changed without `uv run python scripts/batp_schemas.py`.
    assert batp_schemas.main(["--check"]) == 0


@pytest.mark.parametrize("intent", ["AVAILABILITY", "BOOK", "AMEND", "CANCEL", "TALK"])
def test_addressed_intents_take_the_merchant_outside_params(intent: str) -> None:
    params = batp_schemas.schemas()[f"{intent.lower()}.params.json"]

    assert "merchant" not in params["properties"]
    assert "merchant" not in params["required"]


def test_every_intent_has_params_and_result() -> None:
    expected = {
        f"{i.lower()}.{part}.json" for i in batp_schemas.INTENTS for part in ("params", "result")
    }

    assert batp_schemas.schemas().keys() == expected
