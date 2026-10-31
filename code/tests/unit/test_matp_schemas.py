"""The MATP Internet-Draft's JSON Schemas (#63) are generated from `core.models`."""

import matp_schemas
import pytest


def test_committed_schemas_match_core_models() -> None:
    # Fails when a contract model changed without `uv run python scripts/matp_schemas.py`.
    assert matp_schemas.main(["--check"]) == 0


@pytest.mark.parametrize("intent", ["AVAILABILITY", "BOOK", "AMEND", "CANCEL"])
def test_addressed_intents_take_the_merchant_outside_params(intent: str) -> None:
    params = matp_schemas.schemas()[f"{intent.lower()}.params.json"]

    assert "merchant" not in params["properties"]
    assert "merchant" not in params["required"]


def test_every_intent_has_params_and_result() -> None:
    expected = {
        f"{i.lower()}.{part}.json" for i in matp_schemas.INTENTS for part in ("params", "result")
    }

    assert matp_schemas.schemas().keys() == expected
