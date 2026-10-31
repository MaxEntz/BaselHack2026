# Shared contracts

The single source for tool fields, routes and validation rules. Issue [#2](https://github.com/MaxEntz/BaselHack2026/issues/2) froze these files:

- `code/packages/core/src/core/models.py`: tool inputs, outputs and shared values.
- `code/packages/core/src/core/errors.py`: business exceptions and their error codes.
- `code/packages/agent/src/agent/service.py`: the async `AgentService` protocol.
- `code/web/src/types.ts`: chat and live-notification events.

A contract change needs a dedicated PR and a message to the team, with Python and TypeScript changed together.

## Rules (Python models)

### Models
- Models reject unknown fields and field reassignment.
- Collections are tuples in Python and arrays in JSON (deeply immutable, hashable).
- Optional profile and location fields default to `None`. Serialize card payloads with `model_dump(mode="json")`, which keeps the `null`s the TypeScript types expect.

### Identifiers and text
- A slug is `^[a-z0-9-]{3,61}$` (`Slug`).
- `guest_name`, `idempotency_key` and `reservation_id` are stripped and must not be blank.
- No `NonEmptyStr` holds a control character (Unicode `Cc`: NUL, tab, newline…). Spaces (non-breaking included) and letters of any script are kept.
- Guest-side text (`GuestText`: guest names, keys, references) is at most 200 characters.
- `search_merchants`: `query` at most 500 characters, at most 32 `filters.tags` (`gateway.discovery.MAX_QUERY`, `MAX_TAGS`, shared with BATP `DISCOVER`).
- Search filters accept unknown tag strings (reported back in `ignored_filters`) but reject unknown filter names.

### Dates and times
- `HH:MM` is a real 24-hour time.
- Every `starts_at` needs an explicit UTC offset, e.g. `2026-10-31T20:00:00+01:00` (`AwareDatetime`, `Europe/Zurich`).
- A guest's day (`get_availability.date`) and booking time (`starts_at` of a creation or update, also on the public booking API) lie between 2000-01-01 and 2099-12-31 (`BookingDay`, `BookingTime`); outside is `invalid_input`. The bounds are fixed: models never read the clock, the service refuses the past itself.
- These bounds hold at the boundaries (gateway tools, BATP node, platform API); the service trusts what went through them ([#79](https://github.com/MaxEntz/BaselHack2026/issues/79)).

### Floor plan and policy
- A floor plan has at least one table, with unique labels. The builder fills a missing plan before validating.
- A service lasts at least 60 minutes on one day; services don't overlap.
- `seats` may differ from the tables' total: `tables` wins.
- `max_party_size` may exceed the largest table. When no table seats the party, the service raises `PartyTooLarge` (not `SlotUnavailable`), so the guest still gets the fallback URL.
- `update_policy` needs `max_party_size` or `services`.

### Guest identity
- A guest is one name, compared by `agent.service.same_guest`: whitespace runs count as one space, ends are ignored, case is `str.casefold` ("Anna Weiß" = "anna weiss"). Booking, update, cancel and `get_my_reservations` all use it ([#77](https://github.com/MaxEntz/BaselHack2026/issues/77)).
- Guest updates and cancellations need `guest_name`. The protocol's overloads make `by="guest"` without a `guest_name` a type error.
- A guest can't move, resize or cancel a booking once it has started (`slot_unavailable`); only the merchant can.

### Idempotency
- `create_reservation` and `update_reservation` carry an `idempotency_key`, one namespace per merchant and per operation.
- A creation replay with the same key and parameters returns the first booking; other parameters are `invalid_input`.
- `update_reservation` needs `starts_at` or `party_size`. Its key is stored with the change it applied (`public.reservation_changes`): the same key, booking and parameters return the booking as it is now, unchanged; anything else is `invalid_input`. A failed update doesn't use up its key ([#78](https://github.com/MaxEntz/BaselHack2026/issues/78)).
- A replay returns the booking even if it was cancelled since.
- `cancel_reservation` has no key: cancelling twice leaves it cancelled.

### Errors
- The service raises `AgentError` subclasses (`core/errors.py`); only `gateway/fallback.py` turns them into `ToolError` `{error, message, fallback_url?}`.
- `fallback_url` is set for `agent_unavailable` and `party_too_large` only, and missing when the registry itself can't be read.
- Never a stack trace in a response.

### Builder schema
- `MerchantProfile.model_json_schema()` is the source of the builder tool's `parameters`. Tests resolve its `$defs`/`$ref` against `code/tests/fixtures/le-petit-zinc.json`.
- OpenAI strict mode rejects `format: uri` and properties missing from `required`: pass the schema through `builder.llm.strict_schema()` (`code/packages/builder/src/builder/llm.py`). `code/scripts/spike_extract.py` keeps its own copy for the [#4](https://github.com/MaxEntz/BaselHack2026/issues/4) spike only.
- `BuildAgentInput.website` takes any HTTP(S) URL, `localhost` included. The crawler refuses private addresses unless `CRAWL_ALLOW_PRIVATE=1`, and link-local ones (169.254.169.254) always, checking every request and redirect hop after DNS resolution.

## Gateway tools: `/mcp`

Each tool takes flat arguments (the fields of its `*Input` model) and returns `<Result> | ToolError` as a normal result, so Claude sees `fallback_url`. Because the return type is a union, `structuredContent` is wrapped as `{"result": …}`; the text content is the plain JSON object.

| Tool | Input (`core.models`) | Output | Errors (besides `invalid_input`, `agent_unavailable`) |
|---|---|---|---|
| `search_merchants` | `query`, `near?`, `date?`, `filters?: {tags?, max_price?}` | `merchants: [{slug, name, city, tags, price_level, website}]`, `suggestions: [{drop, matches}]`, `ignored_filters` | — |
| `get_availability` | `merchant`, `date`, `time?` (`HH:MM`), `party_size` | `slots: [{starts_at, table_label}]` (≤ 3) | `not_found`, `party_too_large` |
| `create_reservation` | `merchant`, `starts_at`, `party_size`, `guest_name`, `idempotency_key` | `reservation` | `not_found`, `party_too_large`, `slot_unavailable` |
| `update_reservation` | `merchant`, `reservation_id`, `guest_name`, `starts_at?`, `party_size?`, `idempotency_key` | `reservation` | `not_found`, `party_too_large`, `slot_unavailable` |
| `cancel_reservation` | `merchant`, `reservation_id`, `guest_name` | `status: "cancelled"` | `not_found`, `slot_unavailable` |
| `get_my_reservations` | `guest_name` | `reservations`, `notices: [str]` | — |

`reservation` is `{id, merchant, starts_at, party_size, table_label, status, guest_name, via}`.

- Arguments outside a tool's schema are rejected before it runs, as `invalid_input` (`gateway.fallback.RejectedArguments`).
- **Naming a merchant:** a query names one when it holds every distinctive word of its name (not an article or connector in EN/FR/DE, a tag spelling, a generic word like "restaurant", or the merchant's city; `gateway.discovery.names`). A name made only of such words ("La Terrasse") needs the whole name. `search` then keeps only the merchants named.
- `search_merchants` never lists a `down` merchant. When the query names one and no live merchant, it answers `agent_unavailable` with that merchant's `fallback_url`.
- **Deadline:** `AGENT_TIMEOUT_MS` (2 s) bounds the whole call, registry lookup included (`gateway.fallback.deadline_in`). After a `search_merchants` timeout, the named merchant's link lookup gets 10 % more (`LINK_LOOKUP_SHARE`): the call answers within 2.2 s ([#92](https://github.com/MaxEntz/BaselHack2026/issues/92)).
- A timeout on a write may hit after the COMMIT: its message tells Claude to check `get_my_reservations` before retrying.
- **Notices:** `get_my_reservations` returns a notice on every call for 24 real hours after the first call that returned it (`agent.service.NOTICE_WINDOW`), then never again ([#91](https://github.com/MaxEntz/BaselHack2026/issues/91)).
- **Bridge:** with `BATP_URL`, each tool is a BATP request to the node ([PROTOCOL.md §12](PROTOCOL.md#12-the-mcp-bridge)), inside the same 2 s. A lost or overloaded node or an unreadable answer is `agent_unavailable`; a refusal from the node keeps its own code. The gateway still reads the registry for the `down` status and the fallback URL.
- **Log:** one JSON line per call, `{"event": "tool_call", "tool", "merchant", "latency_ms", "outcome"}` (`outcome` = `ok` or the error code), plus `batp_request` as a bridge. Guest names never appear at INFO.
- `GET /health` (no MCP, no token): `200` or `503`, `{"status", "database"}`. It doesn't check the node.

## One merchant's endpoint: `/m/<slug>/mcp`

Same gateway, one restaurant (`gateway/merchant_server.py`, [ADR-0012](adr/0012-one-mcp-server-for-every-merchant-endpoint.md)).

| Tool | Input | Output and errors |
|---|---|---|
| `get_availability`, `create_reservation`, `update_reservation`, `cancel_reservation` | As on `/mcp`, without `merchant` (taken from the path) | As on `/mcp`, same log line (`fallback.call_merchant`) |

- Each description starts with the merchant's sentence from `templates/restaurant.json` (`generated_tools(profile)`), re-read from the registry at each `tools/list`; generic descriptions if the profile can't be read.
- No free text from the website in a description: the cancellation policy never goes in, and name and city are cut to one line of at most 80 characters without control characters.
- Unknown slug, invalid slug or `building` merchant: HTTP `404` before MCP. A `down` merchant keeps its endpoint and answers `agent_unavailable` with its fallback URL. A merchant deployed after startup has its endpoint at once.
- The deadline starts with the slug check and reaches the tool (and `tools/list`) through the ASGI scope's `state`.
- `search_merchants` and `get_my_reservations` stay on `/mcp`. The profile card derives this URL from `mcp_url` (`/mcp` → `/m/<slug>/mcp`).

## Admin tools (platform assistant)

Run by `merchant_platform.tools` on the session's merchant, never exposed to guests. Each result is the card data the merchant sees (or a confirmation sentence).

| Tool | Input (`core.models`) | Result |
|---|---|---|
| `build_agent` | `BuildAgentInput`: `website`, `description` | `BuildAgentOutput`: `profile`, `status: "live"`, `assumptions`, `tools`, `mcp_url`; streams the 5 build steps |
| `list_reservations` | `date` | Rows `{id, time, guest_name, party_size, table_label, via, status}` |
| `get_stats` | `date` | `{bookings, guests, seats, blocked: [HH:MM]}` |
| `block_slot` / `unblock_slot` | `starts_at` | Confirmation |
| `update_policy` | `max_party_size?`, `services?` (at least one) | `Policy`: `{max_party_size, slot_length_min, services}` |
| `update_tables` | `tables: [{label, seats}]` | Confirmation (tables, seats) |
| `cancel_reservation` | `reservation_id` (no `by`: the service gets `by="merchant"`) | Confirmation worded from `cancel_by_merchant`'s outcome |

- `block_slot` / `unblock_slot` refuse an offset that isn't `Europe/Zurich`'s at that instant, with an `invalid_input` naming the local time to send.
- `cancel_by_merchant` returns `notified` (guest notice stored), `ended` (booking over, no notice), `guest_cancelled` or `already_cancelled` (nothing changed). The card never says a notice was sent when none was stored ([#82](https://github.com/MaxEntz/BaselHack2026/issues/82)).
- `update_tables` refuses to remove a table that bookings refer to.

## Platform HTTP API

`merchant_platform.api`, port 8002 (the web chat reaches it through Vite's `/api` proxy).

| Route | Input | Output | Errors |
|---|---|---|---|
| `POST /chat` | `{session_id: str \| null, message: str}` (1–4000 characters) | `ChatEvent` SSE stream, exactly one final `done` | `409` while the session is still answering; a failing turn sends a generic `error` before `done` |
| `GET /session?session_id=` | — | `SessionOutput`: `{merchant: {slug, name} \| null}` | — |
| `GET /events?merchant=<slug>` | — | `LiveEvent` SSE stream | — |
| `POST /public/{slug}/reservations` | `{starts_at, party_size, guest_name, idempotency_key}` | `201` + `{reservation}`, `via="website"` | `ToolError` body: `404` `not_found`, `409` `slot_unavailable`, `422` `party_too_large` / `invalid_input`, `503` `agent_unavailable` |
| `POST /dev/reset-session` | `{session_id: str \| null}` | Restarts onboarding | Disabled with `PLATFORM_DEV_ROUTES=0` |
| `GET /health` | — | Status | — |

- Request validation errors on any route: `422` + `ToolError` (`invalid_input`).
- A `null` chat session is the single POC merchant session.
- `GET /session` answers the merchant the session onboarded or attached, else `PLATFORM_MERCHANT` when registered. It never creates a session. The web keeps its session id in `sessionStorage` and asks at load and after each turn until it knows the merchant ([#88](https://github.com/MaxEntz/BaselHack2026/issues/88)).
- Public booking API: a `building` merchant is `not_found`; a `down` one still takes bookings (the outage only stops the gateway). CORS: `PUBLIC_CORS_ORIGINS` only (default `http://localhost:8080`, `http://127.0.0.1:8080`).

## SSE

| Stream | Events | Notes |
|---|---|---|
| `POST /chat` | `ChatEvent`: `text`, `tool_start`, `step`, `card`, `error`, `done` | One stream per merchant message |
| `GET /events?merchant=` | `LiveEvent`: `new_booking`, `updated`, `cancelled` | Opens with `: connected` |

Each frame uses the JSON object's `type` as its event name, then a blank line:

```text
event: text
data: {"type":"text","delta":"Checking tonight's bookings…"}

event: done
data: {"type":"done"}

```

- Both streams send a `: ping` comment every 15 s. The web gives up on a turn after 90 s without any byte, comments included.
- The server ends a stuck turn: a model call silent for 60 s fails (`assistant.LLM_TIMEOUT`, one retry), and the turn ends with "The assistant is temporarily unavailable".
- **Live events** come from `LISTEN reservations`: each committed INSERT/UPDATE on a registered merchant's `reservations` is re-read from its schema.
  - An UPDATE that changes nothing (a replay) is skipped by the `reservations_skip_noop` trigger and sends nothing.
  - Only guests' changes are sent: a merchant cancellation or a booking with `via = 'merchant'` sends nothing ([#81](https://github.com/MaxEntz/BaselHack2026/issues/81)). An update is always the guest's.
  - `via` is the channel the booking was made through; `date` is its day in `MERCHANT_TZ`.
  - The listener reconnects after 1, 2, 4 then 10 s; each open stream keeps at most 100 pending events (oldest dropped).
- `ChatEvent` and `LiveEvent` live in `core/models.py` and `web/src/types.ts`: change both together.
- Mocks: `code/web/src/mocks/onboarding.json`, `tonight.json` and `cards.json` are ordered arrays of chat events, `notification.json` one live event, `session.json` a `SessionOutput`. The onboarding profile matches the Python fixture's 44-seat plan, and the live booking appears in tonight's reservations.

## Checks

With Python 3.12, uv and Node.js ([minimum version](../code/README.md#prerequisites)), from `code/`:

```sh
uv sync --locked
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict packages/core packages/agent packages/gateway packages/batp packages/merchant_node
npm --prefix web ci
npm --prefix web run check:contracts
```

The TypeScript check compiles the JSON mocks as literals with `satisfies` against `ChatEvent[]`, `LiveEvent` or `SessionOutput`; Python tests validate the same mocks against the Pydantic models. Both run on pull requests.
