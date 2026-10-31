import json
from datetime import date, datetime
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from pydantic import BaseModel, TypeAdapter, ValidationError

from core import models


def test_merchant_profile_round_trip(profile_data: dict[str, Any]) -> None:
    profile = models.MerchantProfile.model_validate(profile_data)

    assert profile.model_dump(mode="json") == profile_data
    assert models.MerchantProfile.model_validate_json(profile.model_dump_json()) == profile


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("slug", "ab"),
        ("slug", "a" * 62),
        ("slug", "Le-Petit-Zinc"),
        ("slug", "le_petit_zinc"),
        ("slug", "../zinc"),
        ("services", []),
        ("services", [{"start": "19:00", "end": "19:00"}]),
        ("services", [{"start": "22:00", "end": "02:00"}]),
        ("services", [{"start": "19:00", "end": "19:30"}]),
        ("services", [{"start": "19:00", "end": "22:00"}, {"start": "21:00", "end": "23:00"}]),
        ("services", [{"start": "19:00", "end": "22:00"}, {"start": "19:00", "end": "22:00"}]),
        ("name", "   "),
        ("tables", []),
        ("tables", [{"label": " ", "seats": 2}]),
        ("tables", [{"label": "T1", "seats": 2}, {"label": "T1", "seats": 4}]),
        ("tags", ["unknown"]),
        ("seats", 0),
        ("max_party_size", 0),
        ("tables", [{"label": "T1", "seats": 0}]),
        ("website", "ftp://example.com"),
        ("fallback_url", "/reservation.html"),
        ("price_level", "cheap"),
        ("unexpected", True),
        ("location", {"city": "Mulhouse", "unexpected": True}),
        ("services", [{"start": "19:00", "end": "22:00", "unexpected": True}]),
    ],
)
def test_merchant_profile_rejects_invalid_data(
    profile_data: dict[str, Any], field: str, value: object
) -> None:
    with pytest.raises(ValidationError):
        models.MerchantProfile.model_validate(profile_data | {field: value})


@pytest.mark.parametrize("slug", ["abc", "a" * 61])
def test_slug_length_boundaries(profile_data: dict[str, Any], slug: str) -> None:
    profile = models.MerchantProfile.model_validate(profile_data | {"slug": slug})

    assert profile.slug == slug


@pytest.mark.parametrize("value", ["9:00", "24:00", "19:60", "99:99", "19:00:00"])
def test_hhmm_rejects_invalid_clock_times(value: str) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(models.HHMM).validate_python(value)


def test_optional_profile_fields(profile_data: dict[str, Any]) -> None:
    del profile_data["location"]
    del profile_data["cancellation_policy"]
    profile = models.MerchantProfile.model_validate(profile_data)

    assert profile.location is None
    assert profile.cancellation_policy is None
    assert models.Location(city="Mulhouse").model_dump() == {
        "city": "Mulhouse",
        "lat": None,
        "lng": None,
    }


def test_profile_and_nested_models_are_frozen(profile_data: dict[str, Any]) -> None:
    profile = models.MerchantProfile.model_validate(profile_data)

    with pytest.raises(ValidationError, match="frozen_instance"):
        profile.name = "Changed"
    with pytest.raises(ValidationError, match="frozen_instance"):
        profile.services[0].start = "11:00"


def test_profile_is_deeply_immutable_and_hashable(profile_data: dict[str, Any]) -> None:
    profile = models.MerchantProfile.model_validate(profile_data)

    assert isinstance(profile.services, tuple)
    assert hash(profile) == hash(models.MerchantProfile.model_validate(profile_data))


@pytest.mark.parametrize(
    "services",
    [
        [{"start": "19:00", "end": "20:00"}],
        [{"start": "12:00", "end": "14:00"}, {"start": "14:00", "end": "16:00"}],
    ],
)
def test_services_accept_one_hour_and_touching_intervals(
    profile_data: dict[str, Any], services: list[dict[str, str]]
) -> None:
    models.MerchantProfile.model_validate(profile_data | {"services": services})


def test_profile_tolerates_party_size_and_seat_mismatches(profile_data: dict[str, Any]) -> None:
    # Maison Albane (#10) and the template default exceed their largest table; #7 trusts `tables`.
    profile = models.MerchantProfile.model_validate(
        profile_data | {"max_party_size": 8, "seats": 1}
    )

    assert profile.max_party_size == 8
    assert profile.seats == 1


@pytest.mark.parametrize(
    ("model", "fields"),
    [
        (
            models.CreateReservationInput,
            {
                "merchant": "le-petit-zinc",
                "starts_at": "2026-10-31T20:00:00+01:00",
                "party_size": 4,
                "guest_name": "Sam Dupont",
                "idempotency_key": "create-1",
            },
        ),
        (
            models.UpdateReservationInput,
            {
                "merchant": "le-petit-zinc",
                "reservation_id": "R-002",
                "guest_name": "Sam Dupont",
                "party_size": 2,
                "idempotency_key": "update-1",
            },
        ),
        (
            models.CancelReservationInput,
            {"merchant": "le-petit-zinc", "reservation_id": "R-002", "guest_name": "Sam Dupont"},
        ),
        (models.GetMyReservationsInput, {"guest_name": "Sam Dupont"}),
        (models.AdminCancelInput, {"reservation_id": "R-002"}),
    ],
)
def test_identity_fields_reject_blank_values(
    model: type[BaseModel], fields: dict[str, object]
) -> None:
    model.model_validate(fields)

    identity = {"guest_name", "idempotency_key", "reservation_id"} & fields.keys()
    assert identity
    for field in identity:
        for blank in ["", "   "]:
            with pytest.raises(ValidationError, match="string_too_short"):
                model.model_validate(fields | {field: blank})


def test_guest_name_is_stripped() -> None:
    assert models.GetMyReservationsInput(guest_name="  Sam Dupont ").guest_name == "Sam Dupont"


def test_update_reservation_requires_a_change() -> None:
    fields = {
        "merchant": "le-petit-zinc",
        "reservation_id": "R-002",
        "guest_name": "Sam Dupont",
        "idempotency_key": "update-1",
    }

    with pytest.raises(ValidationError, match="Provide starts_at or party_size"):
        models.UpdateReservationInput.model_validate(fields)
    assert models.UpdateReservationInput.model_validate(fields | {"party_size": 2}).party_size == 2


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"services": []},
        {"services": [{"start": "19:00", "end": "22:00"}, {"start": "21:00", "end": "23:00"}]},
    ],
)
def test_update_policy_rejects_empty_or_overlapping_changes(fields: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        models.UpdatePolicyInput.model_validate(fields)


def test_update_policy_accepts_a_single_change() -> None:
    assert models.UpdatePolicyInput(max_party_size=6).services is None


@pytest.mark.parametrize("tables", [[], [{"label": "T1", "seats": 2}, {"label": "T1", "seats": 4}]])
def test_update_tables_rejects_invalid_floor_plans(tables: list[dict[str, object]]) -> None:
    with pytest.raises(ValidationError):
        models.UpdateTablesInput.model_validate({"tables": tables})


@pytest.mark.parametrize(
    ("model", "fields"),
    [
        (models.Slot, {"table_label": "T5"}),
        (models.SlotInput, {}),
        (
            models.CreateReservationInput,
            {
                "merchant": "le-petit-zinc",
                "party_size": 4,
                "guest_name": "Sam Dupont",
                "idempotency_key": "create-1",
            },
        ),
        (
            models.UpdateReservationInput,
            {
                "merchant": "le-petit-zinc",
                "reservation_id": "R-002",
                "guest_name": "Sam Dupont",
                "idempotency_key": "update-1",
            },
        ),
        (
            models.Reservation,
            {
                "id": "R-002",
                "merchant": "le-petit-zinc",
                "party_size": 4,
                "table_label": "T5",
                "status": "confirmed",
                "guest_name": "Sam Dupont",
                "via": "assistant",
            },
        ),
    ],
)
def test_booking_times_require_and_preserve_timezone(
    model: type[BaseModel], fields: dict[str, object]
) -> None:
    for starts_at in ["2026-01-15T20:00:00+01:00", "2026-07-15T20:00:00+02:00"]:
        result = model.model_validate(fields | {"starts_at": starts_at})
        assert result.model_dump(mode="json")["starts_at"] == starts_at

    for naive in ["2026-07-15T20:00:00", datetime(2026, 7, 15, 20)]:
        with pytest.raises(ValidationError, match="timezone_aware"):
            model.model_validate(fields | {"starts_at": naive})


@pytest.mark.parametrize(
    ("model", "fields"),
    [
        (models.SearchMerchantsInput, {"query": "bistro"}),
        (models.GetAvailabilityInput, {"merchant": "le-petit-zinc", "party_size": 4}),
        (models.ListReservationsInput, {}),
        (models.GetStatsInput, {}),
    ],
)
def test_tool_dates_are_parsed(model: type[BaseModel], fields: dict[str, object]) -> None:
    result = model.model_validate(fields | {"date": "2026-10-31"})

    assert result.model_dump()["date"] == date(2026, 10, 31)


@pytest.mark.parametrize(
    ("model", "fields"),
    [
        (models.UpdateReservationInput, {"idempotency_key": "update-1"}),
        (models.CancelReservationInput, {}),
    ],
)
def test_update_and_cancel_require_guest_identity(
    model: type[BaseModel], fields: dict[str, object]
) -> None:
    with pytest.raises(ValidationError) as error:
        model.model_validate(fields | {"merchant": "le-petit-zinc", "reservation_id": "R-002"})

    assert [(item["loc"], item["type"]) for item in error.value.errors()] == [
        (("guest_name",), "missing")
    ]


def test_availability_returns_at_most_three_slots() -> None:
    slot = models.Slot.model_validate(
        {"starts_at": "2026-10-31T20:00:00+01:00", "table_label": "T5"}
    )

    assert len(models.GetAvailabilityOutput(slots=[slot] * 3).slots) == 3
    with pytest.raises(ValidationError, match="too_long"):
        models.GetAvailabilityOutput(slots=[slot] * 4)


def test_search_preserves_unknown_tags_for_ignored_filters() -> None:
    filters = models.SearchFilters(tags=["vegan", "wheelchair-accessible"])

    assert filters.tags == ("vegan", "wheelchair-accessible")


def test_profile_schema_is_a_self_contained_tool_input(
    profile_data: dict[str, Any],
) -> None:
    tool = {
        "name": "save_merchant_profile",
        "description": "Save the restaurant profile extracted from the website.",
        "input_schema": models.MerchantProfile.model_json_schema(),
    }
    schema = json.loads(json.dumps(tool))["input_schema"]

    assert schema["type"] == "object"
    Draft202012Validator.check_schema(schema)
    # The fixture exercises every nested definition, resolving their local $refs.
    Draft202012Validator(schema).validate(profile_data)
