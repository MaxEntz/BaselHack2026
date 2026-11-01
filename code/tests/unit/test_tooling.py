"""Both MCP endpoints state one rule for writes, worded for what each one offers (#14, #109)."""

import pytest

from gateway import merchant_server, server, tooling


def flat(text: str) -> str:
    return " ".join(text.split())


@pytest.mark.parametrize(
    ("instructions", "chosen", "calls"),
    [
        (server.INSTRUCTIONS, "a restaurant or a time", "search_merchants or get_availability"),
        (merchant_server.INSTRUCTIONS, "a time", "get_availability"),
    ],
    ids=["shared", "merchant"],
)
def test_each_endpoint_gives_the_rule_for_writes(
    instructions: str, chosen: str, calls: str
) -> None:
    rule = tooling.CONFIRM_RULE.format(chosen=chosen, calls=calls)

    assert rule in flat(instructions)
    assert f"Choosing {chosen} is not a yes" in rule
    assert f"the {calls} call your summary is based on" in rule


@pytest.mark.parametrize("instructions", [server.INSTRUCTIONS, merchant_server.INSTRUCTIONS])
def test_instructions_are_bullets_of_bounded_lines(instructions: str) -> None:
    _, *bullets = instructions.splitlines()

    assert all(line.startswith(("- ", "  ")) for line in bullets)
    assert max(len(line) for line in bullets) <= tooling.INSTRUCTIONS_WIDTH
