# Sésame — Architecture

> Living document: update it **in the same PR** as the code that makes it wrong.
> Decisions and their reasons are in [`adr/`](adr/README.md); the full spec is [`AGENTS.md`](../AGENTS.md).

## 1. In one sentence

A restaurant owner gives us their website and a short description. We generate a booking agent for that restaurant, so that anyone can book a table by asking Claude, and the owner runs it by chatting with our platform. A management site is being added next to the chat ([ADR-0015](adr/0015-give-merchants-a-management-site-next-to-the-chat.md)).

## 2. Overview

```mermaid
flowchart TB
  subgraph USER["User side"]
    U(["User"]) --> C["Claude.ai"]
  end

  subgraph MERCHANT["Merchant side"]
    M(["Merchant"]) --> WEB["Web<br/>code/web: chat, management site"]
  end

  subgraph BACKEND["Our backend"]
    GW["Gateway<br/>MCP server, BATP bridge"]
    NODE["BATP node<br/>127.0.0.1:4710"]
    AG["Agent runtime<br/>AgentService + provisioning"]
    DB[("PostgreSQL 16<br/>public + one schema per merchant")]
    PL["Platform API<br/>FastAPI"]
    ASSIST["Platform assistant<br/>OpenAI tool-use loop"]
    BU["Builder<br/>crawl, extract, validate"]
  end

  C -->|"MCP, Streamable HTTP, via Caddy (HTTPS, Anthropic's IPs only)"| GW
  GW -->|"BATP: one intent per user tool"| NODE
  NODE -->|"AVAILABILITY, BOOK, AMEND, CANCEL, RECALL"| AG
  NODE -->|"DISCOVER: registry"| DB
  AG --> DB
  GW -.->|"down status, fallback URL"| DB
  GW -.->|"agent error or timeout: fallback_url"| SITE["Merchant's own website"]

  WEB -->|"POST /chat (SSE)"| PL
  WEB -->|"management routes"| PL
  PL --> ASSIST
  ASSIST -->|"admin tools"| AG
  PL -->|"same operations"| AG
  ASSIST -->|"build_agent"| BU
  BU -->|"crawl (Playwright)"| SITE
  BU -->|"extract (strict function call)"| OAI["OpenAI API"]
  BU -->|"provision"| AG
  ASSIST -->|"publish_agent: deploy"| AG

  DB -.->|"LISTEN/NOTIFY reservations"| PL
  PL -->|"GET /events (SSE)"| WEB
```

**Two sides, one agent per merchant:**
- **User side.** Claude calls our **gateway**, a remote MCP server added once as a connector. The gateway applies the user's preferences as filters, routes the call to the merchant's agent through the **BATP node** (our agent protocol, [PROTOCOL.md](PROTOCOL.md)), and returns the merchant's own booking page (`fallback_url`) when the agent cannot serve the request.
- **Merchant side.** The merchant chats with the **platform assistant**. It builds the agent from the website (the **builder**), then runs day-to-day operations through admin tools: "What bookings do I have tonight?", "Block 21:00". New bookings from Claude appear live in the chat. A **management site** is being added next to the chat: ordinary pages and forms over the same operations (§3).

**What "agent" means here.** The merchant's agent is not an AI and not a separate process. It is the restaurant's data (tables, hours, policy, bookings) in its own Postgres schema `m_<slug>`, plus the booking tools described for Claude, all served by one shared multi-tenant `AgentService` ([ADR-0001](adr/0001-one-postgres-schema-per-merchant.md)). The only LLMs are:
- Claude, on the user side;
- the builder's extraction step;
- the merchant assistant.

## 3. Main flow: Claude books, the merchant sees it live

```mermaid
sequenceDiagram
  autonumber
  actor U as User
  participant C as Claude.ai
  participant G as Gateway (MCP)
  participant N as BATP node
  participant A as AgentService
  participant DB as PostgreSQL
  participant P as Platform (events)
  participant W as Merchant web chat

  U->>C: Table for 4 tonight at 8pm (preferences: vegetarian, up to €€)
  C->>G: search_merchants(query, filters)
  G->>N: DISCOVER
  N->>DB: live merchants matching tags and price
  N-->>G: merchants, or suggestions of filters to drop
  G-->>C: merchants
  C->>G: get_availability(merchant, date, time, party_size)
  G->>N: AVAILABILITY
  N->>A: get_availability(...)
  A->>DB: tables, policy, blocked slots, confirmed bookings
  A-->>N: up to 3 slots
  N-->>G: slots
  G-->>C: slots
  C->>U: proposes the slots and asks to confirm
  U->>C: Yes, 20:00
  C->>G: create_reservation(..., guest_name, idempotency_key)
  G->>N: BOOK
  N->>A: create_reservation(..., via = claude)
  A->>DB: INSERT reservation, then COMMIT
  DB-->>P: NOTIFY reservations (schema, op, id, status, via)
  A-->>N: reservation R-001
  N-->>G: reservation R-001
  G-->>C: booking details
  C->>U: Booked: R-001, 20:00, table T5
  P-->>W: SSE notification: New booking via Claude
```

**Step by step:**

1. **Request (1).** The user asks Claude in plain language. Their dietary and budget preferences come from Claude's side; we never store them (invariant 5).
2. **Bridge.** Each of Claude's tool calls becomes one BATP request from the gateway to the node, on `127.0.0.1:4710` of the same host ([PROTOCOL.md §12](PROTOCOL.md#12-the-mcp-bridge)). The node serves it through discovery or the `AgentService`. The gateway still reads the registry itself for a merchant's `down` status and fallback URL, so a dead node still gives the restaurant's link. Without `BATP_URL` (development, tests), the gateway calls the `AgentService` in its own process.
3. **Discovery (2–6).** `search_merchants` becomes `DISCOVER`, which reads the registry `public.merchants`. Only merchants with status `live` are returned, filtered by tags and price. If nothing matches, the response suggests which filter to drop and how many merchants that would add (`suggestions`), so Claude can offer it to the user ([ADR-0008](adr/0008-deviations-from-the-original-tool-contracts.md)).
4. **Availability (7–13).** The `AgentService` reads the merchant's schema through `merchant_conn(engine, slug)`. The pure functions in `core/availability.py` compute up to 3 start times closest to the request. Starts are every 30 minutes, the last seating is 60 minutes before the end of the service, and blocked starts are excluded. Each slot gets the smallest free table that fits.
5. **Confirmation (14–15).** Claude must confirm with the user before any write. The tool descriptions instruct it to (invariant 2).
6. **Booking (16–19).** `create_reservation` (BATP `BOOK`) carries an `idempotency_key`, so a retried call does not book twice. The insert either succeeds or is rejected by the database's exclusion constraint if the table is already taken at that time (invariant 1). The reference `R-NNN` comes from the merchant's own sequence.
7. **Live event (20, 25).** Inside the booking's transaction, an `AFTER INSERT` trigger on the merchant's `reservations` table calls `public.notify_reservation()`, which queues `NOTIFY reservations`. Postgres delivers it when the transaction commits. The platform listens on that channel and pushes a `LiveEvent` over SSE to the merchant's open chat: "New booking via Claude". The target is under 2 s.
8. **Answer (21–24).** Claude shows the booking to the user.

**When something fails:**
- A party larger than `max_party_size`, or one that no table can seat, gets `party_too_large`.
- An unreachable or slow agent or node (over 2 s), or a merchant marked `down`, gets `agent_unavailable`.

In both cases the error carries the merchant's `fallback_url`, and Claude gives the user that link. A taken slot gets `slot_unavailable`, with no link.

### Merchant side: two paths to the same operations

> **Status:** being built. The pages come one by one, and Studio's canvas shows the management site and the agent site's address so far. [#127](https://github.com/MaxEntz/BaselHack2026/issues/127) tracks them, and the mockups are in [`design/poc-v10/`](design/poc-v10/README.md).

```mermaid
flowchart LR
  M(["Merchant"]) --> ST["Studio, at /<br/>the chat that builds the sites"]
  M --> MS["Management site, at /manage/slug<br/>pages and forms"]
  ST -->|"POST /chat (SSE)"| ASSIST["Platform assistant"]
  MS -->|"Assistant page: POST /chat (SSE)"| ASSIST
  MS -->|"management routes"| API["Platform API"]
  ASSIST -->|"admin tools"| AG["AgentService"]
  API --> AG
  AG --> DB[("PostgreSQL")]
```

The chat stays the way to onboard and to ask for anything. Pages and forms are a second path, not a second set of rules: a route is a thin adapter over the same operations as the assistant's tool, so a form can't allow what the assistant refuses ([ADR-0015](adr/0015-give-merchants-a-management-site-next-to-the-chat.md)). A secret link opens a merchant's site ([ADR-0018](adr/0018-open-a-merchants-site-with-a-secret-link.md)).

## 4. Components

Paths are relative to `code/`.

| Component | File | Responsibility | Does NOT do |
|---|---|---|---|
| Contracts | `packages/core/src/core/models.py`, `errors.py`, `packages/agent/src/agent/service.py`, `web/src/types.ts` | Tool inputs/outputs, SSE events, business errors, the async `AgentService` protocol, the management site's routes and their shapes (#129) ([CONTRACTS.md](CONTRACTS.md)). `core/forms.py` checks the management forms and names the rule a form breaks | No I/O, no business logic |
| Settings | `packages/core/src/core/config.py` | Typed settings from the environment and `code/.env`; `DATABASE_URL` required | No defaults for secrets |
| Availability rules | `packages/core/src/core/availability.py` | Pure functions: bookable start times, table choice, slot search (up to 3) | No database access, no clock reads |
| DB layer | `packages/core/src/core/db.py`, `migrations/versions/0001_public_schema.py`, `0002_reservation_changes.py`, `0003_agent_requests.py`, `0004_merchant_owners.py`, `0005_notify_actor.py`, `0006_merchant_activity.py`, `0007_agent_request_frames_as_text.py` | Engine; `schema_for(slug)`; merchant tables declared once, with the exclusion constraint, the notify trigger, and a trigger that skips updates that change nothing (an idempotent replay sends no notification); `merchant_conn` (one transaction, schema translation); registry, guest notices, the keys of applied updates, the node's request log and the merchants' owners (the hash of each secret link) and their activity feed in `public` | No business rules, no migrations for merchant schemas |
| Provisioning | `packages/agent/src/agent/provisioning.py`, `packages/agent/src/agent/templates/restaurant.json` | `MerchantProfile` → merchant schema, floor plan, policy, registry row (all or nothing); `deploy` / `set_status` / `deprovision`; default floor plan; generated tool descriptions | Never calls an LLM or builds SQL from profile text |
| Agent service | `packages/agent/src/agent/service.py` (`PgAgentService`), `management.py` (`ManagementService`, the management site's bookings, #134), `dashboard.py` and `activity.py` (Home's data and the activity feed written by every change, #135), `writes.py` (a booking's write on a table, shared by guests and the merchant), `admin.py` | Claude-side bookings: availability, create, update, cancel. Each candidate table is tried in its own SAVEPOINT under a row lock, with its seats re-read under that lock; the exclusion constraint is the guarantee. Merchant operations in `admin.py`: list, stats, block and unblock, policy, floor plan, the management site's forms (hours, floor plan, rules, settings, a day's blocked times, #133), and cancellation with a guest notice (none once the booking is over; the merchant is told which). A `building` merchant is hidden from guests but the merchant can still edit it. Never cancels a booking when a slot is blocked or the policy changes, never removes a table that past bookings refer to | Knows nothing of MCP, HTTP or authentication |
| Gateway | `packages/gateway/src/gateway/server.py`, `__main__.py`, `agent_client.py`, `discovery.py`, `fallback.py`, `merchant_server.py` | Public MCP server on `:8001/mcp` (Streamable HTTP): Host allowlist, optional bearer token, the 6 user tools, preference filtering, 2 s timeout and `fallback_url`, one JSON log line per call, `GET /health`. With `BATP_URL` (deployed), a bridge to the BATP node; without it, calls the `AgentService` in process. Each merchant also has its own endpoint, `/m/<slug>/mcp`: its 4 booking tools without `merchant`, described from its profile ([ADR-0012](adr/0012-one-mcp-server-for-every-merchant-endpoint.md)) | Never stores preferences or exposes admin tools |
| Agent protocol (BATP) | [`documentation/PROTOCOL.md`](PROTOCOL.md) (summary), [`rfc/draft-merchant-agents-batp-00`](rfc/draft-merchant-agents-batp-00.md) (normative Internet-Draft, `make rfc`); `packages/batp/` (messages, framing, status codes, client, generic node); `packages/merchant_node/` (this backend's node, `make node`) | Our agent-only protocol: TCP on port 4710, one JSON frame per line, `HELLO` with the calling agent's identity, intent requests (`DISCOVER`, `AVAILABILITY`, `BOOK`, `AMEND`, `CANCEL`, `RECALL`), status codes with the fallback URL. The node serves the merchant agents through `PgAgentService` and discovery, refuses agent traffic to a `down` (paused) merchant with its fallback URL, and records each request addressed to a merchant, with both frames, in `public.agent_requests` (7 days, #146). The client resends a read or a `RECALL` once after a lost session, never a write | No merchant-side operations; loopback only in the POC |
| Builder | `packages/builder/src/builder/crawl.py`, `extract.py`, `build.py`, `cache.py`, `llm.py` | Website + description → validated `MerchantProfile` → provision → deploy, streamed as steps. Playwright crawl, with every request checked after DNS resolution (SSRF) and redirects followed hop by hop. Extraction by a forced tool call, with the code owning `slug`, `website`, the tags and the floor plan, and one retry on validation errors. On-disk cache (`BUILDER_CACHE`). The homepage's look is read from its computed styles by `look.py`, without the LLM (#149). `llm.py` is the provider-neutral LLM client, shared with the assistant | Never writes SQL, code or CSS; never loads fonts, images or media |
| Platform API | `packages/merchant_platform/src/merchant_platform/api.py`, `management.py`, `auth.py`, `accounts.py`, `overview.py`, `sessions.py`, `logs.py` | Endpoints: `POST /chat` (SSE stream of `ChatEvent`, one turn at a time per session), `GET /events?merchant=` (SSE `LiveEvent`), `GET /session` (the merchant a chat session works on), `POST /public/{slug}/reservations` (the showcase site's form, CORS-limited), `/health`, dev-only `/dev/reset-session`, the owner's `GET /login` / `POST /logout` and `POST /invite` (`accounts.py`: secret links, signed cookies, #132), and the management site's `/merchants/{slug}/…` routes (`management.py`: shapes frozen by #129, behind the owner check of `auth.py`, all built by #132–#146). Also: in-memory sessions, JSON logs for every package it runs (platform, builder, agent; uvicorn's access log stays plain text) | No business logic: delegates to the assistant and the `AgentService` |
| Platform assistant | `packages/merchant_platform/src/merchant_platform/assistant.py`, `tools.py` | Tool-use loop over the admin tools (at most 8 steps per turn) through the `LLMClient` interface (`builder/llm.py`, OpenAI Responses API, `store=False`); onboarding questions; the prompt gives the merchant's registry status, read before every model call, since the Publish button and the pause switch change it outside the chat (#198); `build_agent` runs the builder; `tools.py` runs each tool, logs one `admin_tool` line per call (tool, merchant, latency, outcome; never guest names) and builds its card from the tool's result | Never puts crawled website text in its prompt |
| Live events | `packages/merchant_platform/src/merchant_platform/events.py` | `LiveListener` holds a `LISTEN reservations` connection, reads the booking row for each notification, and turns it into a `LiveEvent` (`new_booking`, `updated`, `cancelled`). `LiveBus` fans it out to that merchant's open `/events` streams | Does not poll the database |
| Web chat | `web/` | Studio at `/` (`src/studio/`): the chat that builds and publishes the sites, its conversation states worked out from the chat events, and its fixed sentences. SSE client, live notifications, mock mode (`VITE_MOCK=1`, `/?gallery`). `src/management/`: the management site's client, one call per `/merchants/{slug}` route, and its mock on the demo fixture (`code/tests/fixtures/demo/la-graine-verte-management.json`, #129, #131); its shell (sidebar, search, clock, notifications, live refresh, #137) and its pages (Home, Opening hours, Booking rules, Settings, Guests and Floor plan so far, #138, #145, #144, #143) | No business logic: the forms send drafts and word the rule the server refused (`problem`) |
| Demo sites | `sites/le-petit-zinc/`, `sites/la-graine-verte/`, `sites/maison-albane/` (`make site`, `make sites` on :8080–:8082) | Fictional restaurant sites (in French): crawl target, fallback URL, and Le Petit Zinc's booking form, which posts to the platform's public booking API | — |
| Demo data | `tests/fixtures/*.json`, `scripts/seed.py`, `demo_reset.py`, `set_status.py`, `demo_management.py`, `export_management_fixture.py` (`make seed`, `demo-reset`, `outage`, `restore`) | 3 fictional Mulhouse restaurants; La Graine Verte's management data, exported from the mockup (#131); reset the demo to a known state; outage switch | Never resets a non-local database |
| Deployment | `deploy/Caddyfile`, `deploy/merchant-gateway.service`, `deploy/merchant-node.service`, `make deploy` | Gateway on the team server behind Caddy (HTTPS, only Anthropic's outbound range gets through), and the BATP node next to it on `127.0.0.1:4710`, both as systemd services. Outside `/opt/merchant-agents` it installs the systemd units, and replaces the Caddyfile and restarts Caddy when it changed, which affects every site on the shared server | Ships neither the platform nor the web app; never exposes port 4710 |

Every component above is built and merged, apart from Studio and the rest of the management site ([#127](https://github.com/MaxEntz/BaselHack2026/issues/127)). Still open: the Claude.ai check of `/m/<slug>/mcp` ([#23](https://github.com/MaxEntz/BaselHack2026/issues/23)) and the Claude Desktop scenarios left to retest ([#14](https://github.com/MaxEntz/BaselHack2026/issues/14)); both are tracked in the [connector runbook](runbooks/connector.md#validation-status).

## 5. Stack and justifications

| Layer | Choice | Why | Rejected alternatives | ADR |
|---|---|---|---|---|
| Language, packaging | Python 3.12, uv workspace (7 packages) | Most mature MCP SDK; MCP, API, crawl and LLM in one language; shared Pydantic contracts | Go/Rust gateway (less mature SDK, splits the contracts) | [0002](adr/0002-python-mcp-v2-fastapi-postgres-react-stack.md) |
| MCP server | `mcp` SDK v2 (`MCPServer`), Streamable HTTP | Claude.ai supports remote MCP servers as connectors; v2 validated by spike #3 | SDK v1 `FastMCP`; a REST API re-exposed as tools | [0002](adr/0002-python-mcp-v2-fastapi-postgres-react-stack.md) |
| Gateway hosting | Team LXC server, Caddy (HTTPS), `sslip.io` name; ngrok as plan B | Fixed public URL, independent of a laptop and of the Wi-Fi | Laptop tunnel only; binding on `0.0.0.0` | [0009](adr/0009-serve-the-gateway-from-the-team-server-behind-caddy.md) |
| Gateway access | Caddy lets only Anthropic's outbound range `160.79.104.0/21` through, `403` for everyone else | Claude.ai can't send a bearer token without the header-auth beta; scans stop at Caddy | Bearer token only; OAuth 2.1; filter in the gateway or in nftables | [0010](adr/0010-allow-only-anthropics-outbound-range-on-the-public-gateway.md) |
| Per-merchant endpoint | One second `MCPServer` on `/m/{slug}/mcp`, slug read from each request, descriptions rewritten at `tools/list` | New merchants get their endpoint without a restart; descriptions follow the policy | One `MCPServer` per merchant behind a dispatcher; a new `BuildAgentOutput` field | [0012](adr/0012-one-mcp-server-for-every-merchant-endpoint.md) |
| Merchant API | FastAPI + Pydantic v2, SSE | Typed endpoints from the shared models; SSE is plain HTTP | Flask, Django | [0002](adr/0002-python-mcp-v2-fastapi-postgres-react-stack.md) |
| Database | PostgreSQL 16, SQLAlchemy 2 (async, psycopg 3), Alembic | Exclusion constraint makes double booking impossible; `LISTEN/NOTIFY` gives live events | SQLite (no exclusion constraint, no NOTIFY) | [0002](adr/0002-python-mcp-v2-fastapi-postgres-react-stack.md) |
| Multi-tenancy | One schema per merchant, `schema_translate_map` | Structural isolation; per-merchant constraint and sequence; no SQL built from slugs | Shared tables + `merchant_id`; one DB per merchant; row-level security | [0001](adr/0001-one-postgres-schema-per-merchant.md) |
| Live events | Postgres `LISTEN/NOTIFY` → SSE | No extra broker; one-way server → browser is enough | Redis pub/sub, WebSockets | [0002](adr/0002-python-mcp-v2-fastapi-postgres-react-stack.md) |
| Agent network | BATP, our own protocol on a dedicated port (4710), with MCP kept as Claude's bridge | A channel reserved for agent traffic, where every request names its agent and its intent; Claude.ai only speaks MCP | Implementing AGTP itself (unstable draft, no clients); JSON over HTTPS on a path; dropping MCP | [0011](adr/0011-our-own-agent-protocol-matp-with-mcp-as-bridge.md), [0014](adr/0014-rename-matp-to-batp.md) |
| LLM (backend) | OpenAI Responses API, forced strict function call | The team's key is an OpenAI one; 160/160 valid extractions in spike #4 | Claude API (no key available); multi-provider layer | [0004](adr/0004-openai-for-backend-llm-calls.md) |
| LLM output | The LLM only fills `MerchantProfile`; code does the rest | A crawled site is untrusted input (prompt injection) | LLM-generated SQL or code | [0003](adr/0003-llm-fills-a-schema-never-generates-code.md) |
| Crawl | Playwright (Chromium) | Handles JavaScript-rendered sites | requests + BeautifulSoup | [0002](adr/0002-python-mcp-v2-fastapi-postgres-react-stack.md) |
| Front | Vite + React + TypeScript, React Router; cards rendered from tool results | A chat and a management site behind a secret link, so no SSR or SEO need; what the merchant sees is what the service returned | Next.js; Markdown tables written by the LLM; chat only | [0002](adr/0002-python-mcp-v2-fastapi-postgres-react-stack.md), [0006](adr/0006-chat-cards-rendered-from-tool-results.md), [0015](adr/0015-give-merchants-a-management-site-next-to-the-chat.md), [0017](adr/0017-router-and-test-tools-for-the-management-site.md) |
| Tests | pytest, pytest-asyncio, testcontainers (Postgres 16); web: Node's runner, Vitest + Testing Library, Playwright | The exclusion constraint and the triggers must be tested for real; pages are checked in a browser against the mockups | Mocked database; screenshot diffs | [0002](adr/0002-python-mcp-v2-fastapi-postgres-react-stack.md), [0017](adr/0017-router-and-test-tools-for-the-management-site.md) |
| Idempotent updates | Each applied update key stored with its change in `public.reservation_changes` | A late retry of an older change never undoes a newer one; existing merchants need no re-provision | A column in each merchant schema (drop and re-provision every merchant) | [0013](adr/0013-store-update-idempotency-keys-in-public.md) |
| Merchant accounts | A secret link per merchant, an HMAC-signed `HttpOnly` owner cookie (stdlib `hmac`) | Guest names, the request log and the OpenAI key stay with whoever built the merchant; no new dependency, and a new link ends every session of the old one | OAuth or e-mail magic links (prove identity, not ownership; need a provider); a server session table; JWT | [0018](adr/0018-open-a-merchants-site-with-a-secret-link.md) |
| Package naming | `merchant_platform` | `platform` shadows a stdlib module | Rely on `sys.path` order | [0005](adr/0005-rename-platform-package-to-merchant-platform.md) |
| Demo reliability | Builder cache (`BUILDER_CACHE`) | Onboarding works even if the Wi-Fi or the API drops | Always live; faked build steps | [0007](adr/0007-builder-cache-for-a-replayable-demo.md) |
| Contracts | Frozen models with deviations from §8 (`guest_name`, suggestions, `update_tables`) | Safe cancel and update; filter relaxation; floor-plan correction | Keep §8 as written; real guest authentication | [0008](adr/0008-deviations-from-the-original-tool-contracts.md) |

## 6. Invariants and assumptions

Breaking one of these is a bug.

1. **No double booking.** Two confirmed reservations never overlap on the same table. The database enforces it with `EXCLUDE USING gist (table_id WITH =, during WITH &&) WHERE (status = 'confirmed')`, and `during` must not be empty (`core/db.py`). A booking re-reads its table's seats under the table's lock, so a floor-plan change never leaves it on a removed or too small table.
2. **Every write from Claude is confirmed; creations and updates are idempotent.** The tool descriptions tell Claude to confirm with the user before `create_reservation`, `update_reservation` or `cancel_reservation`. `create_reservation` and `update_reservation` carry an `idempotency_key`, unique per merchant and per operation; an update's key is stored with the change it applied, so a late retry never undoes a newer change. `cancel_reservation` has no key: cancelling an already cancelled booking must leave it cancelled.
3. **Blocking a slot never deletes a reservation.** It only stops new bookings at that start time.
4. **Never a stack trace in a tool response.** Errors are `{error, message, fallback_url?}` with the codes of AGENTS.md §8.4. `fallback_url` is set for `agent_unavailable` and `party_too_large`. In process or over BATP, a refusal keeps its own code: only an outage or an overload is `agent_unavailable`.
5. **Preferences are never stored server-side.** They only arrive as `filters` on `search_merchants`.
6. **The LLM only produces validated data.** It fills `MerchantProfile`, which Pydantic validates. It never produces SQL, code or tool definitions ([ADR-0003](adr/0003-llm-fills-a-schema-never-generates-code.md)).
7. **Slugs are `^[a-z0-9-]{3,61}$`**, and the schema name is `m_` + slug with `-` → `_` (at most 63 bytes, the Postgres limit). Only `schema_for(slug)` turns a slug into an identifier.
8. **Merchant queries go through `merchant_conn` with Core constructs**, never `text()`, which the schema translation does not rewrite.
9. **Notifications follow commits.** `NOTIFY` is delivered at commit, so a rolled-back booking never reaches the merchant's chat. An `UPDATE` that changes nothing is skipped by a `BEFORE UPDATE` trigger, so a replay sends no notification.
10. **Time is `Europe/Zurich`, stored as `timestamptz`.** Every `starts_at` in a tool input requires an explicit UTC offset (`AwareDatetime`); the platform assistant's `block_slot` / `unblock_slot` also refuse an offset that isn't `Europe/Zurich`'s at that instant (`invalid_input` naming the time to send). `get_availability` takes a date and a local `HH:MM`. Start times follow the local grid; durations and comparisons of instants are done in UTC, so a slot lasts its 120 minutes on a clock-change night and a start in the hour that repeats matches whatever offset it comes back with. Only the first pass of that hour (CEST) is on the grid; times skipped in spring are not.
11. **The merchant's `tables` and `policy` rows are authoritative.** The registry's `profile` column is a display copy that `update_policy` / `update_tables` keep in sync.
12. **Provisioning is all or nothing.** A failed provision leaves no schema and no registry row. A slug in use raises `SlugTaken`.
13. **Contracts change only through a dedicated PR**, with Python and TypeScript updated together.
14. **Only Anthropic's outbound range reaches the public gateway.** On the team server, Caddy answers `403` to any request from outside `160.79.104.0/21`, before uvicorn ([ADR-0010](adr/0010-allow-only-anthropics-outbound-range-on-the-public-gateway.md)). A tunnel URL (plan B) is not covered.
15. **A merchant's cancellation reaches the guest** who made the booking, while it isn't over (no notice after, nor for a booking the merchant entered itself; the merchant is told). Its notice is returned by every `get_my_reservations` (BATP `RECALL`) for 24 real hours after the first call that returned it, so one answer lost in transit doesn't lose it.
16. **A guest changes a booking only before it starts.** `update_reservation` and the guest's `cancel_reservation` answer `slot_unavailable` once its start has passed; only the merchant acts on it after that. At the boundaries (gateway tools, BATP node, platform API), guest-side text holds no control character and at most 200 characters, and guest dates stay between 2000-01-01 and 2099-12-31, so no guest input reaches the database or date arithmetic as a crash.
17. **Only a merchant's owner reads its guests' names on the platform.** Every `/merchants/{slug}` route and `/events` need the owner cookie, which a secret link sets ([ADR-0018](adr/0018-open-a-merchants-site-with-a-secret-link.md)); only the link's hash is stored, and a new link or the merchant's deletion ends the cookies of the old one. `PLATFORM_MERCHANT` (development only) is the exception. Proving that the owner runs the website is out of scope.
18. **Every change writes one activity line, in its own transaction.** Bookings, moves and cancellations from any channel, blocked times, saved settings and pausing each record a line in `public.merchant_activity` on the connection of the change, so a rolled-back change leaves no line and a no-op writes none (#135). A new write path records its line too.

**Assumptions (POC):**
- One city, with no ranking beyond name order.
- Guest identity is the `guest_name` Claude passes, compared everywhere by `same_guest` (case-folded, accents dropped, whitespace collapsed).
- One owner per merchant, identified by a secret link ([ADR-0018](adr/0018-open-a-merchants-site-with-a-secret-link.md)).
- Deployed, the gateway reaches the agents through the BATP node on the same host (`BATP_URL`); without it (development, tests), they run in the same process.

**Open question:** where the platform and the web chat run for the demo, given that live events need them on the gateway's Postgres ([#25](https://github.com/MaxEntz/BaselHack2026/issues/25)).

## 7. Glossary

| Term | Meaning |
|---|---|
| **Protocol / gateway** | Our public remote MCP server, which Claude connects to: discovery, preference filtering, routing, fallback. |
| **Merchant agent** | One merchant's booking logic and data. Logically one per merchant; physically one multi-tenant `AgentService` plus the merchant's schema. |
| **Builder** | The pipeline that turns *website + description* into a validated `MerchantProfile`, then provisions and deploys the agent. |
| **Platform assistant** | The LLM assistant merchants chat with; it acts through admin tools. |
| **Studio** | The chat page where a merchant gives their website and gets their sites built. |
| **Management site** | A merchant's own pages and forms for running their restaurant, in the restaurant's look, next to the chat. |
| **Agent site** | A merchant's agent as agents see it: a `batp://` address and the MCP bridge, with no interface. |
| **`MerchantProfile`** | Validated JSON describing a merchant (hours, tables, tags, price, fallback URL). The single input of provisioning. |
| **Fallback URL** | The merchant's own booking page, returned whenever the agent cannot serve a request. |
| **Registry** | The `public.merchants` table that discovery reads: one row per merchant, with its status (`building`, `live`, `down`). |
| **Slug** | The merchant's identifier (`le-petit-zinc`), which also names its schema (`m_le_petit_zinc`). |
| **Guest notice** | A message stored when the merchant cancels a booking, returned by every `get_my_reservations` (BATP `RECALL`) for 24 hours after the first one that returned it. |
| **MCP** | Model Context Protocol: how an AI client (Claude) discovers and calls tools exposed by a server. |
| **Connector** | A remote MCP server added to Claude.ai. Claude.ai calls it from Anthropic's cloud, hence the need for a public HTTPS URL. |
| **Streamable HTTP** | The MCP transport over HTTP: JSON-RPC requests in POST, responses as JSON or as an SSE stream. |
| **SSE** | Server-Sent Events: a one-way stream of named events from server to browser over a plain HTTP response. |
| **`LISTEN/NOTIFY`** | Postgres publish/subscribe: a session that ran `LISTEN channel` receives every `NOTIFY channel` committed by others. |
| **Exclusion constraint** | A Postgres constraint that rejects any row whose values "conflict" with an existing row, using an operator. Here, the same table (`=`) and overlapping time ranges (`&&`). |
| **`tstzrange`** | A Postgres range of `timestamptz`. A booking occupies `[start, start + 120 min)`. |
| **`schema_translate_map`** | A SQLAlchemy execution option that rewrites a placeholder schema name (`merchant`) into the real one (`m_<slug>`) when statements are compiled. |
| **Idempotency key** | A client-chosen unique key sent with a write, so that retrying the same request has no extra effect. |
| **BATP** | Broad Agent Transfer Protocol: our agent-only protocol (`documentation/PROTOCOL.md`, normative draft in `documentation/rfc/`). |
| **Node** | A BATP server: it serves the merchant agents of one backend on port 4710. |

## 8. Further reading

In this order, to rebuild the project yourself:

1. [`AGENTS.md`](../AGENTS.md): the product, the domain rules (§6), the data model (§7) and the tool contracts (§8). §0 lists what changed since.
2. [`documentation/CONTRACTS.md`](CONTRACTS.md): the frozen Python/TypeScript contracts and the SSE format.
3. [MCP specification](https://modelcontextprotocol.io/specification/latest): what a tool is, how a client calls it. Then [transports](https://modelcontextprotocol.io/specification/latest/basic/transports) for Streamable HTTP.
4. [MCP Python SDK v2, What's new](https://py.sdk.modelcontextprotocol.io/whats-new/) and [Deploy & scale](https://py.sdk.modelcontextprotocol.io/run/deploy/): `MCPServer`, `allowed_hosts` and the 421 error, proxy headers.
5. [Claude custom connectors](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp): eligible plans, authentication options, why the server must be public. Then our [connector runbook](runbooks/connector.md).
6. PostgreSQL 16:
   - [range types](https://www.postgresql.org/docs/16/rangetypes.html) (`tstzrange`, `&&`, empty ranges);
   - [constraints](https://www.postgresql.org/docs/16/ddl-constraints.html) (exclusion);
   - [btree_gist](https://www.postgresql.org/docs/16/btree-gist.html) (why `int =` works in a GiST index);
   - [schemas](https://www.postgresql.org/docs/16/ddl-schemas.html);
   - [NOTIFY](https://www.postgresql.org/docs/16/sql-notify.html) (delivery at commit, payload limits).
7. [SQLAlchemy, translation of schema names](https://docs.sqlalchemy.org/en/21/core/connections.html#translation-of-schema-names): the multi-tenant mechanism, and why `text()` escapes it.
8. [OpenAI function calling](https://developers.openai.com/api/docs/guides/function-calling) and [structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs): forcing a function, strict schemas and their limits.
9. [Playwright for Python](https://playwright.dev/python/docs/intro): headless Chromium for the crawl.
10. [WHATWG, server-sent events](https://html.spec.whatwg.org/multipage/server-sent-events.html): event names, framing, reconnection.
11. [IETF draft, the Idempotency-Key header](https://datatracker.ietf.org/doc/draft-ietf-httpapi-idempotency-key-header/): the idempotency pattern, which our tools reuse as a field.
12. Tooling:
    - [uv workspaces](https://docs.astral.sh/uv/concepts/projects/workspaces/);
    - [FastAPI](https://fastapi.tiangolo.com/);
    - [testcontainers-python](https://testcontainers-python.readthedocs.io/);
    - [Vite](https://vite.dev/guide/) and [React](https://react.dev/learn).
13. [Documenting Architecture Decisions (Michael Nygard)](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions): the ADR format used in [`adr/`](adr/README.md). [Mermaid](https://mermaid.js.org/intro/): the diagrams above.

---

*Last updated: 2026-10-31, describing `main` at commit `110b18a`.*
