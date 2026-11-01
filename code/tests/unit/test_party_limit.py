"""The group limit's bounds, checked once and worded for the form or for the tools (#220)."""

import pytest

from core.errors import FormProblem, InvalidInput
from core.forms import MAX_SEATS, check_party_limit, party_limit_problem


@pytest.mark.parametrize("limit", [1, 6, MAX_SEATS])
def test_a_limit_within_bounds_is_kept(limit: int) -> None:
    assert party_limit_problem(limit) is None
    check_party_limit(limit)


@pytest.mark.parametrize(
    ("limit", "code", "message"),
    [
        (0, "group_too_small", "The largest group is at least 1."),
        (-3, "group_too_small", "The largest group is at least 1."),
        (MAX_SEATS + 1, "too_many_seats", f"The largest group is at most {MAX_SEATS}."),
    ],
)
def test_the_form_names_the_bound(limit: int, code: str, message: str) -> None:
    problem = party_limit_problem(limit)

    assert isinstance(problem, FormProblem)
    assert (str(problem), problem.problem.code) == (message, code)
    if code == "too_many_seats":
        assert problem.problem.seats == MAX_SEATS


@pytest.mark.parametrize("limit", [0, MAX_SEATS + 1])
def test_the_tools_say_the_range_and_the_value(limit: int) -> None:
    with pytest.raises(InvalidInput) as raised:
        check_party_limit(limit)

    assert not isinstance(raised.value, FormProblem)
    assert str(raised.value) == f"max_party_size must be between 1 and {MAX_SEATS}, got {limit}"
