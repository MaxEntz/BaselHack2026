# Shared contracts

The single source for tool fields, routes and validation rules. Issue [#2](https://github.com/MaxEntz/BaselHack2026/issues/2) froze these files:

- `code/packages/core/src/core/models/`: tool inputs, outputs and shared values, in four modules (`base`, `tools`, `management`, `chat`) that `core.models` re-exports.
- `code/packages/core/src/core/errors.py`: business exceptions and their error codes.
- `code/packages/agent/src/agent/protocol.py`: the async `AgentService` protocol, re-exported by `agent.service`.
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
- `search_merchants`: `query` at most 500 characters, at most 32 `filters.tags` (`agent.discovery.MAX_QUERY`, `MAX_TAGS`, shared with BATP `DISCOVER`).
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
- A guest is one name, compared by `agent.guests.same_guest`: whitespace runs count as one space, ends are ignored, accents are dropped, case is `str.casefold` ("Anna Weiß" = "anna weiss", "Léa Müller" = "lea muller"). Booking, update, cancel, `get_my_reservations` and the Guests page all use it ([#77](https://github.com/MaxEntz/BaselHack2026/issues/77)).
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
- `MerchantProfile.look` (#149) is never part of the model's schema: `builder.look.read_look` fills it from the homepage's computed styles (`Page.look`, sampled by `probes.LOOK`, colours and numbers only). Profiles stored before it have `look: null`, which means the default look (#150). Its review-card line is the `look` fact ("Olive, brick and cream. Soft italic titles. Organic shapes.").
- `MerchantProfile.model_json_schema()` is the source of the builder tool's `parameters`. Tests resolve its `$defs`/`$ref` against `code/tests/fixtures/le-petit-zinc.json`.
- OpenAI strict mode rejects `format: uri` and properties missing from `required`: pass the schema through `builder.llm.strict_schema()` (`code/packages/builder/src/builder/llm.py`), the rules the [#4](https://github.com/MaxEntz/BaselHack2026/issues/4) spike found.
- `BuildAgentInput.website` takes any HTTP(S) URL, `localhost` included. The crawler refuses private addresses and ports other than 80 and 443 unless `CRAWL_ALLOW_PRIVATE=1` (#142), and link-local ones (169.254.169.254) always, checking every request and redirect hop after DNS resolution.

## Gateway tools: `/mcp`

Each tool takes flat arguments (the fields of its `*Input` model) and returns `<Result> | ToolError` as a normal result, so Claude sees `fallback_url`. Because the return type is a union, `structuredContent` is wrapped as `{"result": …}`; the text content is the plain JSON object.

| Tool | Input (`core.models`) | Output | Errors (besides `invalid_input`, `agent_unavailable`) |
|---|---|---|---|
| `search_merchants` | `query`, `near?`, `date?`, `filters?: {tags?, max_price?}` | `merchants: [{slug, name, city, tags, price_level, website}]`, `suggestions: [{drop, matches}]`, `ignored_filters` | — |
| `get_availability` | `merchant`, `date`, `time?` (`HH:MM`), `party_size` | `slots: [{starts_at, table_label}]` (≤ 3, `core.models.MAX_SLOTS`) | `not_found`, `party_too_large` |
| `create_reservation` | `merchant`, `starts_at`, `party_size`, `guest_name`, `idempotency_key` | `reservation` | `not_found`, `party_too_large`, `slot_unavailable` |
| `update_reservation` | `merchant`, `reservation_id`, `guest_name`, `starts_at?`, `party_size?`, `idempotency_key` | `reservation` | `not_found`, `party_too_large`, `slot_unavailable` |
| `cancel_reservation` | `merchant`, `reservation_id`, `guest_name` | `status: "cancelled"` | `not_found`, `slot_unavailable` |
| `get_my_reservations` | `guest_name` | `reservations`, `notices: [str]` | — |

`reservation` is `{id, merchant, starts_at, party_size, table_label, status, guest_name, via}`.

- Arguments outside a tool's schema are rejected before it runs, as `invalid_input` (`gateway.fallback.RejectedArguments`).
- **Naming a merchant:** a query names one when it holds every distinctive word of its name (not an article or connector in EN/FR/DE, a tag spelling, a generic word like "restaurant", or the merchant's city; `agent.discovery.query_names`). A name made only of such words ("La Terrasse") needs the whole name. `search` then keeps only the merchants named.
- `search_merchants` never lists a `down` merchant. When the query names one and no live merchant, it answers `agent_unavailable` with that merchant's `fallback_url`.
- **Deadline:** `AGENT_TIMEOUT_MS` (2 s) bounds the whole call, registry lookup included (`gateway.fallback.deadline_in`). After a `search_merchants` timeout, the named merchant's link lookup gets 10 % more (`LINK_LOOKUP_SHARE`): the call answers within 2.2 s ([#92](https://github.com/MaxEntz/BaselHack2026/issues/92)).
- A timeout on a write may hit after the COMMIT: its message tells Claude to check `get_my_reservations` before retrying.
- **Notices:** `get_my_reservations` returns a notice on every call for 24 real hours after the first call that returned it (`agent.notices.NOTICE_WINDOW`), then never again ([#91](https://github.com/MaxEntz/BaselHack2026/issues/91)).
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
| `build_agent` | `BuildAgentInput`: `website`, `description` | `BuildAgentOutput`: `profile`, `status: "building"`, `assumptions`, `facts` (`BuildFact`: `key`, `label`, `value`, `source` = a crawled page's path, `"you"` or null, `quote`), `flags` (`BuildFlag`: `title`, `detail`), `tools`, `mcp_url`; streams 4 steps, then `deploy` `waiting` ("Waiting for you.") (#136) |
| `publish_agent` | `PublishAgentInput` (empty) | Confirmation "Your agent is live: Claude users can now book you."; streams `deploy` `running` then `done`. A live agent stays live, a paused (`down`) one is refused (#136) |
| `update_rules` | `cancellation_policy` (empty removes it) | `diff` card; "Saved. Claude reads this before it cancels for a guest." The largest group stays `update_policy`'s (#152) |
| `try_request` | `party_size`, `date?`, `time?` | What an agent gets: the times, or the 413 with the booking page; "Agents get a 404 until you publish" while `building` |
| `send_test_booking` | (none) | A booking for 2 at the first free time, made through `AgentService.create_reservation` with `via="claude"` as the node's `BOOK` would; refused while `building` |
| `update_look` | `accent?`, `accent_2?` (`#rrggbb`), `display_font?`, `corners?`, `reset` | `diff` card(s), "See it" opens the management pane. The main colour must read as the small titles' colour, at 4.5:1 on `bg` and `surface` (`titled()`, the web's own steps); the second one at 3:1 on `bg` (`readable()`). The buttons' text (`on_accent`) must read at 4.5:1 on both colours and their 8 % darker shades (`fit_text()`, #209): it stays if it does, else it turns white or near-black; if neither reads, the colour just picked moves away from it, else the other one does. Each change is said, with its own `diff` card. `reset` reads the website's look again. Stored in the registry's profile copy, read by `GET /merchants/{slug}` |
| `show_view` | `view`: `agent` or `manage`; `moment?`: `morning`, `before`, `during` or `after`, on `manage` (#154) | A `view` card: Studio switches its canvas, the plain chat shows nothing |
| `link_page` | `page`: `reservations`, `guests`, `hours`, `floor`, `rules`, `agent` or `settings` | A `page` card: the management site's panel shows "You can change this in <Opening hours>." with the link (code's words, `tools.PAGE_LINKS` and the web's `PAGE_LINKS`); Studio and the plain chat show nothing. Only in a reply that calls no other tool (#140) |
| `list_reservations` | `date` | Rows `{id, time, guest_name, party_size, table_label, via, status}` |
| `get_stats` | `date` | `{bookings, guests, seats, blocked: [HH:MM]}` |
| `block_slot` / `unblock_slot` | `starts_at` | Confirmation |
| `update_policy` | `max_party_size?`, `services?` (at least one) | `Policy`: `{max_party_size, slot_length_min, services}` |
| `update_tables` | `tables: [{label, seats}]` | Confirmation (tables, seats) |
| `cancel_reservation` | `reservation_id` (no `by`: the service gets `by="merchant"`) | Confirmation worded from `cancel_by_merchant`'s outcome |

- `block_slot` / `unblock_slot` refuse an offset that isn't `Europe/Zurich`'s at that instant, with an `invalid_input` naming the local time to send.
- `cancel_by_merchant` returns `notified` (guest notice stored), `ended` (booking over, no notice), `own_booking` (the merchant made it, no notice; from #134), `guest_cancelled` or `already_cancelled` (nothing changed); `core.models.MerchantCancelOutcome`. The card never says a notice was sent when none was stored ([#82](https://github.com/MaxEntz/BaselHack2026/issues/82)).
- `update_tables` refuses to remove a table that bookings refer to.
- `AgentService.set_status(slug, "down" | "live")` pauses or resumes a published agent ([#146](https://github.com/MaxEntz/BaselHack2026/issues/146)): `down` sends Claude and other agents to the booking page, while the website keeps booking. A `building` merchant is `invalid_input` (only `deploy` publishes), an unknown one `not_found`. The Agent page calls it through `POST /agent/pause` and `/agent/resume`; not an admin tool of the assistant.

## BATP node request log

- One row per answered request addressed to a published merchant (`live` or `down`) in `public.agent_requests` (migrations `0003`, `0007`, [#146](https://github.com/MaxEntz/BaselHack2026/issues/146)): the caller's `agent_name`, the intent, the status and both frames as `text`, never re-serialized: the response exactly as sent, the request as the node read it (compact, unknown fields left out). `HELLO`, and so the caller's token, is never stored.
- Written after the answer, in its own task (`NodeServer(record=…)`), through the log's own engine (at most 2 connections, apart from the handlers'); a record is dropped with a warning when 100 are already waiting. The log never delays or fails an answer, not even another request's.
- Rows older than 7 days are purged as new ones arrive. Rows hold guest names: only the merchant's own session may read them.
- `AgentService.list_agent_requests(slug, day, refused=)` reads them for `GET /agent/requests`: the merchant's local day, newest first, at most 10. `agent.requests.describe` words each row from its frames:
  - `AVAILABILITY` 200: "{agent} checked {time} for {n} guests" / "{day}. Your agent offered the closest free times." ("free times" without a time);
  - `BOOK` 200: "{guest} booked through {agent}" / "{day}, {time}, {n} guests, table {T}."; `AMEND` 200: "{guest} changed their booking through {agent}"; `CANCEL` 200: "{guest} cancelled through {agent}";
  - 413: "A group of {n} was sent to your booking page" / "Your limit is {max}, so the guest got your link instead." (the limit now; "No table fits them" when the group is within it);
  - 409: "{guest} asked for a time that had just gone" / "{agent} offered them another one."; `CANCEL`: "{guest} tried to cancel a booking that had started" / "Bookings can only be changed before they start."; `AMEND` of a booking that had started (the error message ends with `core.errors.STARTED`): "{guest} tried to change a booking that had started", same text; `AMEND` without `starts_at` (more guests at the same time): "{guest} asked for more seats than were free" / "Their booking stays as it was." ([#188](https://github.com/MaxEntz/BaselHack2026/issues/188));
  - 404, 503 and 400 have their own sentences; anything else is "{agent}: {intent}, answered {status}".
  - `{day}` is the mockup's short day, "Sat 31 Oct", in English whatever the server's locale. The demo fixture's rows are worded exactly as the server words them (`test_agent_requests_wording.py`), and `export_management_fixture.py` words them with `describe`, not the mockup.

## Platform HTTP API

`merchant_platform.api`, port 8002 (the web chat reaches it through Vite's `/api` proxy); the routes live in `chat.py`, `live.py`, `public.py`, `auth.py` and `management.py`.

| Route | Input | Output | Errors |
|---|---|---|---|
| `POST /chat` | `{session_id: str \| null, message: str}` (1–4000 characters) | `ChatEvent` SSE stream, exactly one final `done` | `409` while the session is still answering; `403` `needs_invite` for an onboarding without the invitation; a failing turn sends a generic `error` before `done` |
| `GET /session?session_id=` | — | `SessionOutput`: `{merchant: {slug, name} \| null}` | — |
| `GET /events?merchant=<slug>` | — | `LiveEvent` SSE stream | `401` `not_signed_in`, `403` `other_merchant` |
| `GET /login?token=` | — | `303` to `/manage/<slug>`, with the owner cookie | `401` `not_signed_in` for an unknown link |
| `POST /logout` | — | `204`, the owner cookie removed | — |
| `POST /invite` | `{code: str}` | `204`, with the invite cookie (nothing when no code is set) | `403` `needs_invite` for a wrong code |
| `POST /session/claim` | `{session_id: str}` | `204`, with the owner cookie | `401` `not_signed_in` unless a build in this process attached that session within 5 minutes, its link is current, and it wasn't claimed yet |
| `POST /public/{slug}/reservations` | `{starts_at, party_size, guest_name, idempotency_key}` | `201` + `{reservation}`, `via="website"` | `ToolError` body: `404` `not_found`, `409` `slot_unavailable`, `422` `party_too_large` / `invalid_input`, `503` `agent_unavailable` |
| `POST /dev/reset-session` | `{session_id: str \| null}` | Restarts onboarding | Disabled with `PLATFORM_DEV_ROUTES=0`; the platform refuses to start with it on beside an https `PLATFORM_WEB_URL`, a cookie secret or an invite code |
| `GET /health` | — | Status | — |

- Request validation errors on any route: `422` + `ToolError` (`invalid_input`).
- A `null` chat session is the development merchant's shared session: without `PLATFORM_MERCHANT`, `POST /chat` needs a `session_id` other than `default` (`422` otherwise).
- **Accounts (#132, [ADR-0018](adr/0018-open-a-merchants-site-with-a-secret-link.md)).**
  - A build gives its merchant an owner and a secret link, `<PLATFORM_WEB_URL>/api/login?token=…`. The link is shown once, as the profile card's `login_url`; the model and the chat history never see it. Only the token's SHA-256 is stored (`public.merchant_owners`). `make owner-link SLUG=… [NAME=…]` issues a new one, and the old link stops working.
  - `GET /login` sets `sesame_owner`: `HttpOnly`, `SameSite=Lax`, `Path=/`, 30 days, and `Secure` when `PLATFORM_WEB_URL` is `https`. The cookie is signed with `PLATFORM_COOKIE_SECRET` (a random key per process when unset) and names the merchant and its current link. A new link or the merchant's deletion ends it.
  - The owner cookie is needed by every `/merchants/{slug}` route and by `/events`. An open `/events` stream checks it again at each event and heartbeat, and ends once the link is replaced or the merchant deleted.
  - A chat session acts with the link that attached it (a cookie, or the build that created the merchant), checked on every message. A cookie attaches a session without a merchant, or renews its link; another merchant's cookie is `403`. Once a cookie bound the session, or its build claimed one, every message needs that cookie (`401` without). A session whose link was replaced, or whose merchant was deleted, is forgotten with its history, its running turn stopped, and answers `401`. `/session` reports the session's own merchant while its link is current, else the cookie's, else `PLATFORM_MERCHANT`.
  - After a build, the web calls `POST /session/claim`, once, so that the building browser gets the owner cookie and its live events open at once. The card's link opens the site anywhere else.
  - With `PLATFORM_INVITE_CODE` set, a session that works on no merchant needs the invite cookie, set by `POST /invite` (the web sends `/?invite=<code>`), before any model call. A cookie owner or `PLATFORM_MERCHANT` needs none.
  - `PLATFORM_MERCHANT` is development only: its pages and events open without a link. The platform refuses to start when it comes with an https `PLATFORM_WEB_URL`, a cookie secret or an invite code, or when the secret is under 32 characters or the code under 8.
  - `POST /logout` removes this browser's cookie; only a new link ends the others. An anonymous caller refused by the invite leaves no session behind.
  - Cross-origin requests never carry the cookies: CORS allows no credentials.
  - The token never reaches a log line, nor does a session id, which acts for its merchant: uvicorn's access log masks `token=` and `session_id=`.
- `GET /session` answers the merchant the session onboarded or attached, else `PLATFORM_MERCHANT` when registered. It never creates a session. The web keeps its session id in `sessionStorage` and asks at load and after each turn until it knows the merchant ([#88](https://github.com/MaxEntz/BaselHack2026/issues/88)).
- Public booking API: a `building` merchant is `not_found`; a `down` one still takes bookings (the outage only stops the gateway). CORS: `PUBLIC_CORS_ORIGINS` only (default `http://localhost:8080`, `http://127.0.0.1:8080`).

## SSE

| Stream | Events | Notes |
|---|---|---|
| `POST /chat` | `ChatEvent`: `text`, `tool_start`, `step`, `site_page`, `card`, `error`, `done` | One stream per merchant message. `site_page`: one per page the crawl read, right after `crawl` is done (#151) |
| `GET /events?merchant=` | `LiveEvent`: `new_booking`, `updated`, `cancelled` | Opens with `: connected` |

Each frame uses the JSON object's `type` as its event name, then a blank line:

```text
event: text
data: {"type":"text","delta":"Checking tonight's bookings…"}

event: done
data: {"type":"done"}

```

- `BuildFact.box` says where a fact's quote sits on its page's screenshot, in the image's pixels; null without a screenshot, or when the words can't be found on it (#151).
- Both streams send a `: ping` comment every 15 s. The web gives up on a turn after 90 s without any byte, comments included.
- The server ends a stuck turn: a model call silent for 60 s fails (`assistant.LLM_TIMEOUT`, one retry), and the turn ends with "The assistant is temporarily unavailable".
- **Live events** come from `LISTEN reservations`: each committed INSERT/UPDATE on a registered merchant's `reservations` is re-read from its schema.
  - An UPDATE that changes nothing (a replay) is skipped by the `reservations_skip_noop` trigger and sends nothing.
  - Only guests' changes are sent: a merchant cancellation, a booking with `via = 'merchant'`, or any change a merchant operation made, sends nothing ([#81](https://github.com/MaxEntz/BaselHack2026/issues/81)). A merchant operation marks its transaction (`core.db.as_merchant`, the transaction-local `sesame.actor`), and `notify_reservation()` passes it on as `actor` (migration `0005`, #134): a move from the management site is the merchant's, an update by `update_reservation` the guest's.
  - `via` is the channel the booking was made through; `date` is its day in `MERCHANT_TZ`.
  - The listener reconnects after 1, 2, 4 then 10 s; each open stream keeps at most 100 pending events (oldest dropped).
- `ChatEvent` and `LiveEvent` live in `core.models` (`chat.py`) and `web/src/types.ts`: change both together.
- Mocks: `code/web/src/mocks/onboarding.json`, `tonight.json` and `cards.json` are ordered arrays of chat events, `notification.json` one live event, `session.json` a `SessionOutput`. The onboarding profile matches the Python fixture's 44-seat plan, and the live booking appears in tonight's reservations.

## Management site (#129)

The management site's routes sit under `/merchants/{slug}` (`merchant_platform/management.py`).
Their shapes are frozen here and in `web/src/types.ts`, and every route of the table is built.
`not_built_yet` (`501`) stays in the contract for a route frozen before it is built.

Every route depends on `auth.owner_of_path`, so none can ship open: `401` `not_signed_in` without
the merchant's owner, `403` `other_merchant` for another merchant, before the body is read. The
owner is whoever opened the merchant's secret link (#132, ADR-0018); `PLATFORM_MERCHANT` stands in
for it in local development only.

| Route | Body | Answer | Issue |
|---|---|---|---|
| `GET /` | | `MerchantOverview`: name, city, price, tags, website, status, timezone, the server's clock (`now`), the agent's BATP address, its MCP URL, the management URL, `owner_name`, `demo`, `look` (the site's, #149; null: the default look) | #132 |
| `GET /site` | | `PublicSite`: the screenshots stored with the build, `{path, width, height, image}` with `image` a `data:` URL; no pages for a merchant built before #151 | #151 |
| `POST /agent/try` | `AgentTryInput` (`intent`) | `AgentTryOutput`: both frames as text and the node's `status`. One real frame through the BATP node as `urn:batp:agent:sesame-studio`, with the platform choosing its params; the node's refusals are answers (`404` until published). `agent_unavailable` without `BATP_URL`. BOOK makes a real booking under the test guest | #151 |
| `POST /publish` | | `MerchantOverview`, now `live`: publishes the agent a build left `building`. A paused (`down`) agent is refused with `invalid_input` | #136 |
| `GET /assistant` | | `AssistantConversation`: `{session_id, items}`, the owner's conversation with the assistant. One platform-made session per owner link (`o-` + 24 random bytes), shared by every device signed in with it; `/chat` on it still needs the owner's cookie. `items` are the web chat's shapes (`user`, `assistant`, `card`, `notification`, `error`; never the steps), the last 200, kept in memory until the platform restarts | #140 |
| `GET /dashboard` | | `Dashboard`: `history` (30 days ending `date`, today), `next_days` (7), `channels` (bookings over `history`), `room`, `activity` (latest 6, `ACTIVITY_LINES`) | #135 |
| `GET /reservations?date=` | | `ReservationDay`: the day's services, every start of its grid, slot length, largest group, blocked times and bookings, cancelled ones included | #134 |
| `POST /reservations` | `MerchantBookingInput` | `201` + `ManagedReservation`, `via="merchant"` | #134 |
| `PATCH /reservations/{id}` | `MoveBookingInput` | `ManagedReservation` | #134 |
| `POST /reservations/{id}/cancel` | | `MerchantCancelOutput`: the booking and `outcome` | #134 |
| `GET /slots?date=&party_size=&ignore=` | | `DaySlots`: every start of the day that hasn't passed, with the table it would get (or none) and whether it is blocked; `ignore` leaves a booking out, for a move | #134 |
| `GET /guests?q=` | | `GuestsOutput`: one row per guest (`same_guest`) whose name holds `q` and who has a live booking ahead in the next 7 days: the earliest and how many, sorted by name | #144 |
| `GET /search?q=` | | `SearchOutput`: up to 8 bookings (`SEARCH_LIMIT`) of the next 7 days by guest name or reference | #135 |
| `GET`/`PUT /hours` | `HoursInput` | `Hours` | #133 |
| `GET`/`PUT /tables` | `FloorPlanInput` | `FloorPlanOutput` | #133 |
| `GET`/`PUT /rules` | `RulesInput` | `Rules`, with the sentence each tool's description starts with on `/m/<slug>/mcp` | #133 |
| `GET`/`PUT /settings` | `SettingsInput` | `MerchantSettings` | #133 |
| `GET /blocked`, `PUT /blocked?date=` | `BlockedDayInput` (`PUT` only) | `BlockedTimes`: the blocked times to come | #133 |
| `POST /agent/pause`, `POST /agent/resume` | | `AgentStatusOutput` (`down`, `live`); resuming a `building` merchant is `invalid_input` (it is published, #136) | #146 |
| `GET /agent/requests?date=&refused=` | | `AgentRequests`: newest first, at most 10 (`MAX_AGENT_REQUESTS`) | #146 |

**Days, times and text**
- Days are `YYYY-MM-DD` and times `HH:MM`, local to the merchant (`MerchantOverview.timezone`).
  Instants (`starts_at`, `created_at`, `at`) carry their offset.
- Booking inputs take `BookingTime` and `GuestText`, as on the guest side. References, `ignore`
  and `q` refuse control characters and stay within 200 characters (#79).
- `PUT /blocked` takes the day's whole set as local `HH:MM` (at most 48), so the web never
  computes an offset (#83). The last write wins.
- Merchant text refuses control characters. Name and city (`ShortText`) are at most 80
  characters, and blank means none for the city. The cancellation policy (`PolicyText`) is one
  paragraph of at most 500 characters: the web turns new lines into spaces. Table labels are at
  most 20 characters.
- A form holds at most 8 services and 100 tables. A table, like the largest group, has at most
  1000 seats.
- The web checks lengths and URL syntax before sending, as the mockup does. Past them, and for
  a blank table label, the server answers a generic `422` without `problem`.
- `GET` answers stay within these bounds, so a form saves back what it loaded: #133 cuts a
  stored policy to one paragraph of 500 characters, since the builder keeps the site's text.

**Forms answer the rule they break**
- Form bodies (`HoursInput` of `ServiceDraft`, `FloorPlanInput` of `TableDraft`, `RulesInput`,
  `SettingsInput`) are structural. `core.forms` checks their rules and raises a `FormProblem`,
  which the API answers as `422` `invalid_input` with `problem`. A validating type would only
  give a generic 422, with no rule and no row.
- Problem codes:
  - `no_services`;
  - `service_incomplete` (`services: [i]`: a start or an end is missing or blank, as a cleared
    time input sends `""`);
  - `service_too_short` (`services: [i]`);
  - `services_overlap` (`[i, j]`, `i < j`);
  - `no_tables`, `duplicate_table`, `table_without_seats` (`table`);
  - `too_many_seats` (`seats`: the most allowed; with `table` for a table, without it for the
    largest group);
  - `group_too_small`, `name_required`.
- Indices refer to the list as sent. Answers list services by start.
- Three rules need the merchant's data, so the service (#133) checks them with the same codes:
  - `table_in_use` (`table`, `guest`): a booking refers to a removed table;
  - `table_too_small` (`table`, `guest`, `seats`);
  - `booking_page_off_site`.
- Bodies hold only their input's fields. `PUT /rules` with the `Rules` that `GET /rules`
  answered is `422`.

**Errors**
- Errors are a `ManagementError`: a `ToolError`'s fields, `problem`, and `error` as one of the
  §8.4 codes or the management site's own (`not_signed_in`, `other_merchant`, `not_built_yet`).
  `ErrorCode` stays the list the tools answer. Statuses otherwise are those of the public
  booking API.

**Bookings**
- A merchant moves a booking only before it starts (`slot_unavailable` otherwise), within its
  day (`invalid_input` otherwise), and the table is picked again. `MerchantCancelOutcome` adds `own_booking` to #82's
  outcomes: cancelled without a notice, since the merchant made the booking itself (#134). The
  admin tool's card words it too.

**Bookings from the management site (#134, `agent/management.py`)**
- `POST /reservations` books through `create_reservation(via="merchant")`, with the web's idempotency key (one namespace with the guests' creations).
- `PATCH /reservations/{id}` moves a booking that hasn't started to a start of the same day that isn't blocked or past, with the smallest free table that seats the party (its own first among equals), under the table's lock. Its key is stored in `public.reservation_changes`, one namespace with the guests' updates (ADR-0013): a replay returns the booking as it is now, the same key for another move is `invalid_input`. It sends no live event. Moving a booking a guest made stores a guest notice ("… moved your booking R-012 on 2026-11-01 to 20:30."), returned like a cancellation's.
- `POST /reservations/{id}/cancel` answers `outcome`. A guest notice is stored only for a booking a guest made (through an agent or the website): one the merchant entered itself is `own_booking`, no notice.
- `GET /slots`: the day's starts that haven't passed, each with the table `pick_table` would give (none when the party exceeds `max_party_size` or no table is free) and `blocked`. With `ignore` (a move), the table the move would give: its own first among equals, and no party limit for the booking's own party. `until` is computed in UTC, so it is right on clock-change nights.

**The restaurant's forms (#133, `agent/site_forms.py`, hours and floor plan through `agent/admin.py`)**
- Each save runs in one `merchant_conn` transaction and keeps the registry's columns and
  `profile` in step with the merchant's rows, so the gateway's discovery, the fallback link and
  the tool descriptions on `/m/<slug>/mcp` follow at the next call, with no restart.
- `GET /hours` lists services by start; `PUT /hours` is `update_policy(services=…)`, which stores
  them by start too, like `provision`, so the same hours in another order are no change ([#201](https://github.com/MaxEntz/BaselHack2026/issues/201)).
- `GET /tables` answers the plan in the order it was last saved. `PUT /tables` is
  `update_tables`: tables are matched by label (a rename is a removal plus an addition). It refuses
  to remove a table a booking refers to (`table_in_use`, upcoming or past) or to shrink one below an
  upcoming booking's party (`table_too_small`, `seats` = the party), naming the first such booking's
  guest.
- `PUT /rules` saves `max_party_size`, `fallback_url` and `cancellation_policy` together. The
  booking page must stay on the merchant's website: same host (`www.` aside) and same port, else
  `booking_page_off_site`. `slot_length_min` stays read-only. A stored policy reads as one
  paragraph of at most 500 characters (`agent.provisioning.one_line`); saved back as it was read,
  it keeps its full stored text and writes no activity line ([#201](https://github.com/MaxEntz/BaselHack2026/issues/201)); an
  edited one replaces the whole stored text with what was sent.
- `PUT /settings` saves name, city, price and tags (duplicates dropped); the website stays.
- `PUT /blocked?date=` sets the day's blocked starts still to come, the ones `GET /blocked`
  lists. Every time must be one of the day's starts (`invalid_input` otherwise, nothing changed);
  a start still to come that isn't sent is reopened. Starts that have passed never change: the
  day's past blocks stay, and a past time sent is ignored.
  It writes one activity line: the times it blocked, else the ones it reopened. `GET /blocked` and
  its answer list the blocked starts that haven't passed.

**Dashboard**
- The dashboard is for the merchant's today by the server's clock, which it answers as `date`;
  a `?date=` other than that day is `invalid_input`. `MerchantOverview.now` gives the same clock,
  so the pages take their today, top-bar clock and day bars from it rather than from the
  browser (the mock's clock starts at the fixture's `now`).
- `DayHistory`:
  - `bookings` counts a day's live bookings;
  - `guests` and `agent_guests` count the guests in them, through an agent meaning any `via`
    but `merchant` and `website`;
  - `cancelled` counts the day's cancelled bookings.
- `channels` counts bookings, so its percentage and its caption agree.
- `room` describes today's current or next service, or its last one once they are over:
  - `seats` of the floor plan, `guests` booked in it, and which tables have a live booking;
  - the next free start in it that isn't blocked, for `party_size` (4, or the largest group
    when smaller);
  - `largest_group`: `min(max_party_size, largest table)`.
- `GuestRow.bookings` counts the live bookings that haven't started.
- `ActivityItem.text` is worded by the server when read, relative to `date` ("R-009, Sara
  Muller, 2 guests, today 18:30"). `source` is the channel that made the change (`merchant` for
  the merchant's own). One line per change; a `PUT /blocked` is one change.

**Home's data (#135, `agent/dashboard.py`, `agent/activity.py`)**
- Every change writes one line in `public.merchant_activity` (migration `0006`), in the transaction of the change, whichever path made it:
  - new bookings, from an agent, the website, the assistant or the management site;
  - guests' and the merchant's moves and cancellations;
  - blocked and reopened times;
  - saved hours, rules, floor plan and settings;
  - pausing and resuming.
  A change that fails, or changes nothing, writes none: a time already blocked, a pause of a paused agent, an identical save, a move to the same time, a replayed key. A guest's own update or cancellation is the agent's (`source: claude`), since the website only books; saving hours and the party limit together writes one line each. Lines are dated by the service's clock.
- A line keeps its `details` (guest, party, start; the times; the form saved), not its words, so `text` is worded when read. A line without them reads as its reference: one bad row never takes Home down. `make seed` loads the mockup's 12 lines with their details.
- `GET /dashboard` makes one query per block, whatever the history: the days (the 30 before and the 7 after, together), channels, the room, activity. On the demo data it answers in about 20 ms.
- `GET /search?q=`: up to 8 bookings of the next 7 days by guest name or reference, cancelled ones included; names compare as on the Guests page (`agent.guests.guest_key`: case, accents and spacing ignored, so "zoe" finds Zoé); a blank `q` finds nothing.
- `room.next_free` is null both when the service is full and when its last seating has passed (after 20:30 for an 18:30–21:30 dinner): the web words the second case itself.

**Other fields**
- `MerchantOverview`:
  - addresses are absolute (`management_url` is the web app's URL, `PLATFORM_WEB_URL` from #132,
    followed by `/manage/<slug>`);
  - `owner_name` is what the owner asked to be called, or `null` until known;
  - `demo` says the merchant was built from one of the demo sites in `code/sites/`.
- `Rules.tools` is `agent.provisioning.described_tools(profile)`: what an AI agent reads, so the
  cancellation policy reads "see the restaurant's own website".
- `AgentRequest.request` and `.response` are the BATP frames as sent, JSON strings; never a
  `HELLO` or its token. `refused` is `status >= 400`.
- No answer exposes the registry's stored `profile`.

**Web client and mock**
- `web/src/management/client.ts` has one call per route (`ManagementClient`), throwing
  `ManagementRequestError` with the status and the `ManagementError`.
- With `VITE_MOCK=1`, `managementClient` (`web/src/management/index.ts`) is the mock. It
  serves the demo fixture, `code/tests/fixtures/demo/la-graine-verte-management.json`,
  exported from the mockup (#131, `scripts/export_management_fixture.py`); `make seed` loads the
  same data into Postgres. The assistant's opening conversation is next to it,
  `la-graine-verte-assistant.json`: `{ merchant, opening }`, `opening` being the web chat's items
  (`web/src/chat/state.ts`) without `id` and `turn`.
- The mock applies the server's rules, as listed above:
  - no past start, the smallest free table then its label, unknown fields refused;
  - a reused key with other parameters refused;
  - the problems, the tool descriptions rewritten.
- It sends the fixture's `live_booking` once, 6 s after the first subscription, to every
  subscriber. Its reference stays free until then. It keeps its table, unless the merchant took
  that table meanwhile.
- Its clock starts at the fixture's `now`, which needs a `±HH:MM` offset.
- The management shell subscribes through `ManagementClient`. The chat mock's own notification
  belongs to the chat page.

## Checks

With Python 3.12, uv and Node.js ([minimum version](../code/README.md#prerequisites)), from `code/`:

```sh
uv sync --locked
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict packages/core packages/agent packages/gateway packages/batp packages/merchant_node packages/builder packages/merchant_platform
npm --prefix web ci
npm --prefix web run check:contracts
```

The TypeScript check compiles the JSON mocks as literals with `satisfies` against `ChatEvent[]`, `LiveEvent`, `SessionOutput` or, for the management fixture, `ManagementFixture`; Python tests validate the same mocks against the Pydantic models, check that the fixture is La Graine Verte's (floor plan, hours, rules, tool descriptions), that today's history matches its bookings and that no two live bookings overlap on a table, and that each management model and union has the same fields and values as its web type. Both run on pull requests.
