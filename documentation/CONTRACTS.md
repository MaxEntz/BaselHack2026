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
the assistant reads the tool's schema.

`BuildAgentInput.website` accepts any HTTP(S) URL, including `localhost` for the
demo site. The crawler refuses private addresses unless `CRAWL_ALLOW_PRIVATE=1` (#21),
and link-local ones (the cloud metadata endpoint 169.254.169.254) in every case. It
checks every browser request, redirect hops included, after DNS resolution.

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

## Gateway tools

Each tool returns `<Result> | ToolError`, as a normal result that the assistant can read, so that it
sees `fallback_url`. A `ToolError` never arrives as a protocol error or an `isError` result.
Because the return type is a union, the SDK wraps `structuredContent` as `{"result": ...}`; the
text content is the plain JSON object.

- Arguments outside the tool's schema are rejected before the tool runs and answer
  `invalid_input` (`gateway.fallback.RejectedArguments`).
- `search_merchants` never lists a `down` merchant (#12). When the query names one and no live
  merchant, it answers `agent_unavailable` with that merchant's `fallback_url`. Otherwise a
  request for that restaurant would silently propose others.
- Without the registry there is no URL to give: `agent_unavailable` has no `fallback_url`.
- A timeout on a write may hit after the COMMIT, so its message asks the assistant to check
  `get_my_reservations` before retrying.
- Each call logs one JSON line: `{"event": "tool_call", "tool", "merchant", "latency_ms",
  "outcome"}`, where `outcome` is `ok` or the error code. Guest names never appear at INFO.

With `MATP_URL` set, the gateway is a bridge (`documentation/PROTOCOL.md`, §12): `MatpAgentClient`
sends each tool to the node as a MATP request, and the 2 s budget includes that hop. A lost node
or an invalid result is `agent_unavailable`. The gateway still reads the registry itself, for the
`down` status and the fallback URL, so that a dead node still gives the link. For the same reason,
when `DISCOVER` fails and the query names a merchant, `search_merchants` answers with its link.
The log line then also has `matp_request`, the id of the request sent to the node.

`GET /health` (outside MCP, no token): `200` or `503`, with `{"status", "database"}`. It doesn't
check the node.

### Each merchant's own endpoint: `/m/<slug>/mcp` (#23)

The same gateway serves each merchant on its own (`gateway/merchant_server.py`, ADR-0012). It has
4 tools: `get_availability`, `create_reservation`, `update_reservation` and
`cancel_reservation`. They have the same arguments as the shared tools minus `merchant`, which
comes from the path, and the same results, errors and log line (`fallback.call_merchant`).

- Each tool description starts with the merchant's sentence from `templates/restaurant.json`
  (`generated_tools(profile)`), for example "Check free tables at Le Petit Zinc (Mulhouse).
  Services: 12:00-14:00, 19:00-22:00. Groups up to 6.". It is read from the registry's `profile`
  at each `tools/list`, so a policy change shows at the next listing. If the profile can't be
  read, the generic descriptions stay.
- Tool descriptions read as the connector's instructions, and a profile's free text comes from a
  website anyone can onboard. So the cancellation policy never goes in ("Policy: see the
  restaurant's own website"), and the name and city are cut to one line of at most 80 characters,
  without control characters. Services and party size are validated values.
- An unknown slug, a slug outside `^[a-z0-9-]{3,61}$` or a `building` merchant: HTTP `404`
  before MCP. A `down` merchant keeps its endpoint, and its tools answer `agent_unavailable` with
  the fallback URL. A merchant deployed after startup has its endpoint at once.
- `search_merchants` and `get_my_reservations` stay on the shared `/mcp`.
- The profile card derives this URL from `mcp_url`: `/mcp` → `/m/<slug>/mcp`.

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
  merchant's schema. An UPDATE that changes nothing (an idempotent replay) is skipped by
  the `reservations_skip_noop` trigger and sends nothing. The listener reconnects after 1, 2, 4 then 10 s; each open stream
  keeps at most 100 pending events (oldest dropped).
- `POST /public/{slug}/reservations` (the showcase site's form) with `{starts_at,
  party_size, guest_name, idempotency_key}`: `201` + `{"reservation": Reservation}`
  booked `via="website"`. A merchant still `building` is `not_found`; a `down` one keeps
  taking bookings here (the outage only stops assistants, at the gateway). Errors use the
  `ToolError` body with `404` (`not_found`), `409`
  (`slot_unavailable`), `422` (`party_too_large`, `invalid_input`), `503`
  (`agent_unavailable`). CORS allows `PUBLIC_CORS_ORIGINS` only (default
  `http://localhost:8080` and `http://127.0.0.1:8080`).
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
uv run mypy --strict packages/core packages/agent packages/gateway packages/matp packages/merchant_node
npm --prefix web ci
npm --prefix web run check:contracts
```

The TypeScript check compiles the actual JSON mocks as literals with `satisfies`
against `ChatEvent[]` or `LiveEvent`. Python tests validate the same mocks against
the Pydantic `ChatEvent` and `LiveEvent`. Both checks run on pull requests.
