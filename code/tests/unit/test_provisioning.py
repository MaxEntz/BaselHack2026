"""Template, default floor plan and generated tools: the deterministic side of provisioning."""

import json
from pathlib import Path
from typing import Any

import pytest

from agent.provisioning import TEMPLATE, default_tables, generated_tools, load_template
from core.models import MerchantProfile, TableSpec


def plan(tables: list[TableSpec]) -> list[tuple[str, int]]:
    return [(table.label, table.seats) for table in tables]


def test_default_tables_for_40_seats() -> None:
    assert plan(default_tables(40)) == [
        ("T1", 4),
        ("T2", 4),
        ("T3", 4),
        ("T4", 4),
        ("T5", 4),
        ("T6", 4),
        ("T7", 4),
        ("T8", 4),
        ("T9", 2),
        ("T10", 6),
    ]


def test_default_tables_for_12_seats() -> None:
    assert plan(default_tables(12)) == [("T1", 4), ("T2", 4), ("T3", 4)]


@pytest.mark.parametrize(
    ("seats", "expected"),
    [
        (1, [("T1", 2)]),
        (5, [("T1", 4), ("T2", 2)]),
        (7, [("T1", 4), ("T2", 2), ("T3", 2)]),
        (29, [*[(f"T{n}", 4) for n in range(1, 8)], ("T8", 2)]),
        (30, [*[(f"T{n}", 4) for n in range(1, 7)], ("T7", 6)]),
    ],
)
def test_default_tables_round_the_remainder_up_to_two_seat_tables(
    seats: int, expected: list[tuple[str, int]]
) -> None:
    tables = default_tables(seats)

    assert plan(tables) == expected
    assert sum(table.seats for table in tables) - seats in (0, 1)


@pytest.mark.parametrize("seats", [0, -4, 401])
def test_default_tables_refuse_an_implausible_seat_count(seats: int) -> None:
    with pytest.raises(ValueError, match="seats"):
        default_tables(seats)


def test_generated_tools_fill_every_placeholder(profile_data: dict[str, Any]) -> None:
    tools = generated_tools(MerchantProfile.model_validate(profile_data))

    assert [tool.name for tool in tools] == [tool.name for tool in TEMPLATE.tools]
    assert tools[0].description == (
        "Check free tables at Le Petit Zinc (Mulhouse). "
        "Services: 12:00-14:00, 19:00-22:00. Groups up to 6."
    )
    assert all("{" not in tool.description and "}" not in tool.description for tool in tools)


def test_generated_tools_without_city_or_cancellation_policy(
    profile_data: dict[str, Any],
) -> None:
    profile = MerchantProfile.model_validate(
        {**profile_data, "location": None, "cancellation_policy": None}
    )

    descriptions = {tool.name: tool.description for tool in generated_tools(profile)}

    assert "(unknown city)" in descriptions["get_availability"]
    assert descriptions["cancel_reservation"].endswith("Policy: none stated.")


def test_template_defaults_match_agents_md() -> None:
    assert TEMPLATE.defaults.slot_length_min == 120
    assert TEMPLATE.defaults.max_party_size == 8
    assert [(s.start, s.end) for s in TEMPLATE.defaults.services] == [("19:00", "22:00")]


def write_template(tmp_path: Path, **changes: Any) -> Path:
    data = {**TEMPLATE.model_dump(mode="json"), **changes}
    path = tmp_path / "restaurant.json"
    path.write_text(json.dumps(data))
    return path


@pytest.mark.parametrize(
    ("description", "reported"),
    [
        ("Call {phone}.", "phone"),
        ("Book at {name.upper}.", "name.upper"),
        ("Book at {}.", "allowed"),
        ("Book at {name:{width}}.", "width"),
        ("Book at {name!z}.", "conversion"),
        ("Groups up to {max_party_size:q}.", "format code"),
    ],
)
def test_load_template_rejects_descriptions_that_do_not_render(
    tmp_path: Path, description: str, reported: str
) -> None:
    path = write_template(
        tmp_path, tools=[{"name": "get_availability", "description": description}]
    )

    with pytest.raises(RuntimeError, match=rf"(?s)restaurant\.json.*{reported}"):
        load_template(path)


def test_load_template_rejects_invalid_defaults(tmp_path: Path) -> None:
    path = write_template(
        tmp_path, defaults={**TEMPLATE.defaults.model_dump(), "slot_length_min": 0}
    )

    with pytest.raises(RuntimeError, match=r"(?s)restaurant\.json.*slot_length_min"):
        load_template(path)
