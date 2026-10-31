# Shared contracts

Issue [#2](https://github.com/MaxEntz/BaselHack2026/issues/2) freezes these interfaces:

- `code/packages/core/src/core/models.py`: tool inputs, outputs and shared values.
- `code/packages/core/src/core/errors.py`: business exceptions and their error codes.
- `code/packages/agent/src/agent/service.py`: async `AgentService` protocol.
- `code/web/src/types.ts`: chat and live-notification events.

Contract changes require a dedicated PR and a message to the team.

## Python

Models reject unknown fields and field reassignment. Slugs contain 3–61 lowercase
ASCII letters, digits or hyphens. `HH:MM` values use a 24-hour clock; services must
end after they start on the same day. Booking timestamps require an explicit
offset, such as `2026-10-31T20:00:00+01:00` (Europe/Zurich).

Optional profile and location fields default to `None`. Serialize card payloads
with `model_dump(mode="json")`, preserving nullable fields for the TypeScript
output types. Search filters deliberately accept unknown tag strings so discovery
can report them in `ignored_filters`.

The service raises `AgentError` subclasses. The gateway converts them to
`ToolError`, adding a fallback URL for `agent_unavailable` and `party_too_large`.
Guest updates and cancellations require `guest_name`; merchant cancellation uses
`by="merchant"` on the service.

`MerchantProfile.model_json_schema()` is the builder tool's `input_schema`.
Tests validate the schema and resolve its local `$defs`/`$ref` entries against
`code/tests/fixtures/le-petit-zinc.json`. Live Anthropic acceptance is tracked by
the extraction spike in [#4](https://github.com/MaxEntz/BaselHack2026/issues/4).

## SSE

`POST /chat` returns one `ChatEvent` stream per merchant message, ending in `done`.
`GET /events?merchant=<slug>` sends `LiveEvent` notifications. Each frame uses
the JSON object's `type` as its SSE event name, followed by a blank line:

```text
event: text
data: {"type":"text","delta":"Checking tonight's bookings…"}

event: done
data: {"type":"done"}

```

`code/web/src/mocks/onboarding.json` and `tonight.json` are ordered arrays of chat
events. `notification.json` is one live event. The onboarding profile matches the
Python fixture and the 44-seat plan from #10; the live booking also appears in
tonight's reservations.

## Checks

With Python 3.12, uv and Node.js 24 installed, run from `code/`:

```sh
uv sync --locked
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict packages/core packages/agent
npm --prefix web ci
npm --prefix web run check:contracts
```

The TypeScript check compiles the actual JSON mocks as literals with `satisfies`
against `ChatEvent[]` or `LiveEvent`. Python tests also validate their card payloads
against the shared Pydantic models. Both checks run on pull requests.
