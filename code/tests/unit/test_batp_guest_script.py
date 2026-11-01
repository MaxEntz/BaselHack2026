"""The guest's own AI agent (#252), without a node: what its model may call, what it prints, and
where it sends its token."""

import batp_guest
import pytest


def test_the_model_can_discover_and_talk_but_never_confirm() -> None:
    tools = {spec.name: spec.parameters for spec in batp_guest.SPECS}
    assert set(tools) == {"discover", "talk"}
    # `confirm` is the person's: code sends it after their yes, the model has no field for it.
    assert set(tools["talk"]["properties"]) == {"merchant", "message"}
    assert set(tools["discover"]["properties"]) == {"query", "tags"}
    assert all(schema["additionalProperties"] is False for schema in tools.values())


def test_what_the_node_sent_is_printed_without_control_characters() -> None:
    summary = "A table for 2\x1b[2J at 20:00.\x07\nUnder Lena Koch."
    assert batp_guest._printable(summary) == "A table for 2[2J at 20:00.\nUnder Lena Koch."


async def test_the_token_goes_to_a_node_on_this_machine_only(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("BATP_TOKENS", f'{{"{batp_guest.GUEST.id}": "{"t" * 24}"}}')

    assert await batp_guest.run(["Table for 2", "--url", "batp://191.96.88.152:4710"]) == 2
    assert "Not sending the token to 191.96.88.152" in capsys.readouterr().err
