# Shared contracts

Issue [#2](https://github.com/MaxEntz/BaselHack2026/issues/2) freezes these interfaces:

- `code/packages/core/src/core/models.py`: tool inputs, outputs and shared values.
- `code/packages/core/src/core/errors.py`: business exceptions and their error codes.
- `code/packages/agent/src/agent/service.py`: async `AgentService` protocol.
- `code/web/src/types.ts`: chat and live-notification events.

Contract changes require a dedicated PR and a message to the team.

## Python

Models reject unknown fields and field reassignment. Collections are tuples in
Python and arrays in JSON, so instances are deeply immutable and hashable. Slugs
contain 3–61 lowercase ASCII letters, digits or hyphens. `HH:MM` values use a
24-hour clock. A service lasts at least 60 minutes on the same day, and services
must not overlap. Booking timestamps require an explicit offset, such as
`2026-10-31T20:00:00+01:00` (Europe/Zurich).

`guest_name`, `idempotency_key` and `reservation_id` are stripped and must not be
blank on tool inputs. `update_reservation` needs `starts_at` or `party_size`, and
`update_policy` needs `max_party_size` or `services`.

A floor plan has at least one table and unique labels. The builder must fill a
missing plan before validating (#22). Two mismatches stay valid on purpose:

- `seats` may differ from the tables' total: `tables` is the source of truth (#7).
- `max_party_size` may exceed the largest table (template default, Maison Albane in
  #10). The service must therefore raise `PartyTooLarge`, not `SlotUnavailable`,
  when no table can seat the party, so that the guest still gets the fallback URL.

Optional profile and location fields default to `None`. Serialize card payloads
with `model_dump(mode="json")`, preserving nullable fields for the TypeScript
output types. Search filters deliberately accept unknown tag strings so discovery
can report them in `ignored_filters`; unknown filter names are rejected, because
Claude reads the tool's schema.

`BuildAgentInput.website` accepts any HTTP(S) URL, including `localhost` for the
demo site. The crawler refuses private addresses unless `CRAWL_ALLOW_PRIVATE=1` (#21).

The service raises `AgentError` subclasses. The gateway converts them to
`ToolError`, adding a fallback URL for `agent_unavailable` and `party_too_large`.
Guest updates and cancellations require `guest_name`; merchant cancellation uses
`by="merchant"` on the service, which is why the admin tool input has no `by`.
The protocol's overloads make `by="guest"` without a `guest_name` a type error.

`MerchantProfile.model_json_schema()` is the source of the builder tool's `parameters`.
Tests validate the schema and resolve its local `$defs`/`$ref` entries against
`code/tests/fixtures/le-petit-zinc.json`. OpenAI strict mode accepts `$defs`/`$ref`
but rejects `format: uri` (from `HttpUrl`) and any property missing from `required`
(the `None` defaults): pass the schema through `strict_schema()` from
`code/scripts/spike_extract.py`, as validated live by the extraction spike in
[#4](https://github.com/MaxEntz/BaselHack2026/issues/4).

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

`ChatEvent` and `LiveEvent` exist in `core/models.py` and `web/src/types.ts`; change
both together. `code/web/src/mocks/onboarding.json`, `tonight.json` and `cards.json`
are ordered arrays of chat events. `notification.json` is one live event. The
onboarding profile matches the Python fixture and the 44-seat plan from #10; the
live booking also appears in tonight's reservations.

## Platform HTTP API

`merchant_platform.api` (port 8002; the web chat reaches it through Vite's `/api` proxy):

- `POST /chat` with `{"session_id": str | null, "message": str}` (1–4000 characters):
  the `ChatEvent` stream above, always ending with exactly one `done`. A failing turn
  sends a generic `error` (details only in the logs) before `done`. `409` while the same
  session is still answering. A `null` session is the single POC merchant session.
- `GET /events?merchant=<slug>`: `LiveEvent` stream, with an SSE comment every 15 s. Fed
  by `LISTEN reservations`: every committed INSERT/UPDATE on a registered merchant's
  `reservations` becomes `new_booking`, `updated` or `cancelled`, re-read from the
  merchant's schema. The listener reconnects after 1, 2, 4 then 10 s; each open stream
  keeps at most 100 pending events (oldest dropped).
- `POST /public/{slug}/reservations` (the showcase site's form) with `{starts_at,
  party_size, guest_name, idempotency_key}`: `201` + `{"reservation": Reservation}`
  booked `via="website"`. Errors use the `ToolError` body with `404` (`not_found`), `409`
  (`slot_unavailable`), `422` (`party_too_large`, `invalid_input`), `503`
  (`agent_unavailable`). CORS allows `PUBLIC_CORS_ORIGINS` only (default
  `http://localhost:8080`).
- Request validation errors on any route are `422` + `ToolError` (`invalid_input`).
- `POST /dev/reset-session` with `{"session_id": str | null}` restarts onboarding (disabled
  with `PLATFORM_DEV_ROUTES=0`). `GET /health`.

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
against `ChatEvent[]` or `LiveEvent`. Python tests validate the same mocks against
the Pydantic `ChatEvent` and `LiveEvent`. Both checks run on pull requests.
