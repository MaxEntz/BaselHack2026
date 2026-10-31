"""Export the JSON Schema of every BATP intent's `params` and `result` (#63).

The Internet-Draft's appendix points to these files, so they come from `core.models` and
never by hand. Run from `code/`:

    uv run python scripts/batp_schemas.py           # write documentation/rfc/schemas/
    uv run python scripts/batp_schemas.py --check   # fail if the committed files are stale
"""

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel

from core.models import (
    CancelReservationInput,
    CreateReservationInput,
    GetAvailabilityInput,
    GetAvailabilityOutput,
    GetMyReservationsInput,
    GetMyReservationsOutput,
    ReservationOutput,
    SearchMerchantsInput,
    SearchMerchantsOutput,
    UpdateReservationInput,
)

SCHEMAS = Path(__file__).resolve().parents[2] / "documentation" / "rfc" / "schemas"
DIALECT = "https://json-schema.org/draft/2020-12/schema"


class PingParams(BaseModel):
    """`PING` takes no params: `{}`."""


class PingResult(BaseModel):
    pong: Literal[True]
    server_time: AwareDatetime


# Intent -> (params, result, the request's `merchant` moves out of params). PROTOCOL.md §7.
INTENTS: dict[str, tuple[type[BaseModel], type[BaseModel], bool]] = {
    "DISCOVER": (SearchMerchantsInput, SearchMerchantsOutput, False),
    "AVAILABILITY": (GetAvailabilityInput, GetAvailabilityOutput, True),
    "BOOK": (CreateReservationInput, ReservationOutput, True),
    "AMEND": (UpdateReservationInput, ReservationOutput, True),
    "CANCEL": (CancelReservationInput, ReservationOutput, True),
    "RECALL": (GetMyReservationsInput, GetMyReservationsOutput, False),
    "PING": (PingParams, PingResult, False),
}


def schemas() -> dict[str, dict[str, Any]]:
    """File name -> schema, for every intent's params and result."""
    out: dict[str, dict[str, Any]] = {}
    for intent, (params, result, addressed) in INTENTS.items():
        name = intent.lower()
        params_schema = params.model_json_schema(mode="validation")
        if addressed:
            # On the wire, the merchant is the request's `merchant`, not a param.
            params_schema["properties"].pop("merchant")
            params_schema["required"].remove("merchant")
        out[f"{name}.params.json"] = _document(params_schema, f"BATP {intent} params", params)
        out[f"{name}.result.json"] = _document(
            result.model_json_schema(mode="serialization"), f"BATP {intent} result", result
        )
    return out


def _document(schema: dict[str, Any], title: str, model: type[BaseModel]) -> dict[str, Any]:
    source = f"{model.__module__}.{model.__qualname__}".replace("__main__", "batp_schemas")
    return {"$schema": DIALECT, "$comment": f"Generated from {source}", **schema, "title": title}


def _render(schema: dict[str, Any]) -> str:
    return json.dumps(schema, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export the BATP intents' JSON Schemas.")
    parser.add_argument("--check", action="store_true", help="fail if the files are stale")
    args = parser.parse_args(argv)
    expected = {name: _render(schema) for name, schema in schemas().items()}
    if args.check:
        stale = [
            name
            for name, text in expected.items()
            if not (SCHEMAS / name).is_file() or (SCHEMAS / name).read_text() != text
        ]
        extra = sorted({p.name for p in SCHEMAS.glob("*.json")} - expected.keys())
        for name in stale + extra:
            print(f"stale: documentation/rfc/schemas/{name}", file=sys.stderr)
        if stale or extra:
            print("Run: uv run python scripts/batp_schemas.py", file=sys.stderr)
            return 1
        return 0
    SCHEMAS.mkdir(parents=True, exist_ok=True)
    for path in SCHEMAS.glob("*.json"):
        if path.name not in expected:
            path.unlink()
    for name, text in expected.items():
        (SCHEMAS / name).write_text(text)
    print(f"{len(expected)} schemas in documentation/rfc/schemas/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
