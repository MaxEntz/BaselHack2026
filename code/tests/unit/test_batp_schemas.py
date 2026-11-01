"""The BATP Internet-Draft's JSON Schemas (#63) are generated from `core.models`."""

import batp_schemas
import pytest

from batp.messages import ADDRESSED, ALL_INTENTS


def test_committed_schemas_match_core_models() -> None:
    # Fails when a contract model changed without `uv run python scripts/batp_schemas.py`.
    assert batp_schemas.main(["--check"]) == 0


@pytest.mark.parametrize("intent", sorted(ADDRESSED))
def test_addressed_intents_take_the_merchant_outside_params(intent: str) -> None:
    params = batp_schemas.schemas()[f"{intent.lower()}.params.json"]

    assert "merchant" not in params["properties"]
    assert "merchant" not in params["required"]


def test_every_intent_has_params_and_result() -> None:
    expected = {
        f"{i.lower()}.{part}.json" for i in batp_schemas.INTENTS for part in ("params", "result")
    }

    assert batp_schemas.schemas().keys() == expected


def test_every_intent_of_the_protocol_has_its_schemas_and_no_other() -> None:
    # A closed intent loses its schemas with it (#254); a new one can't ship without them.
    assert set(batp_schemas.INTENTS) == set(ALL_INTENTS)
