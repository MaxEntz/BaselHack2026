# Sésame — code

Spec: [`AGENTS.md`](../AGENTS.md) (also loaded by Claude Code through `CLAUDE.md`). Run every command from this `code/` directory.

## Prerequisites

- [uv](https://docs.astral.sh/uv/) (it installs Python 3.12 itself if needed)
- Docker (Postgres 16, and testcontainers for the integration tests)
- Node.js ≥ 22.18 (Vite, and `npm test`, which runs `.ts` files as is). This is the one place the minimum is stated; `web/package.json` `engines` matches it
- Linux only, once: `uv run playwright install --with-deps chromium` (installs Chromium's system packages, needs sudo rights)

## First run

```bash
make setup     # .env from .env.example, uv sync, Playwright Chromium, npm ci in web/
make db        # Postgres 16 via docker compose, waits until healthy
make migrate   # alembic upgrade head
make test
```

Port 5432 already taken on your machine? Set `DB_PORT` and the port in `DATABASE_URL` in `.env` (e.g. 5433).

## Run the services

| Command | What | URL |
|---|---|---|
| `make gateway` | MCP server (protocol), `uv run python -m gateway`: the 6 user tools (`talk_to_merchant` instead of the 4 booking ones with `GATEWAY_TALK`), JSON logs, `GET /health` | http://localhost:8001/mcp |
| `make node` | BATP node (our agent protocol, `documentation/PROTOCOL.md`), `uv run python -m merchant_node` | batp://127.0.0.1:4710 |
| `make platform` | Platform API, `uv run uvicorn merchant_platform.api:app --port 8002` | http://localhost:8002 |
| `make web` | Studio and the management site (Vite) | http://localhost:5173 |
| `make site` | Demo restaurant website "Le Petit Zinc" (static, in French) | http://localhost:8080 |
| `make sites` | The 3 demo websites: Le Petit Zinc, La Graine Verte, Maison Albane | http://localhost:8080, :8081, :8082 |

Le Petit Zinc's booking form posts to `http://localhost:8002/public/le-petit-zinc/reservations` (#15); served from the team server, to `https://sesame.<server>/api/…` (#142). Open it at the `localhost` URL that `make site` prints, not at the `127.0.0.1` link `http.server` prints: the platform only accepts the form from the origins in its CORS list. Without the platform, the form asks guests to call.

Claude.ai needs a public HTTPS URL: `make deploy` ships the gateway to the team server (`https://191-96-88-152.sslip.io/mcp`). See [`documentation/runbooks/connector.md`](../documentation/runbooks/connector.md). The web app, the platform and the demo sites run on the same server, each on its own host (#142): see [`documentation/runbooks/platform.md`](../documentation/runbooks/platform.md).

## Demo data

The demo's script, checklist and plan B: [`documentation/runbooks/demo.md`](../documentation/runbooks/demo.md). Three fictional restaurants in Mulhouse, in `tests/fixtures/` (also the `demo_profiles` pytest fixture):

| Slug | Tags | Price | Services | Tables | Max party |
|---|---|---|---|---|---|
| `le-petit-zinc` | vegetarian, gluten-free, terrace, kid-friendly | €€ | 12:00–14:00, 19:00–22:00 | 4×2, 6×4, 2×6 | 6 |
| `la-graine-verte` | vegan, vegetarian, gluten-free | € | 11:30–14:30, 18:30–21:30 | 6×2, 3×4 | 4 |
| `maison-albane` | vegetarian, terrace | €€€€ | 19:30–22:30 | 5×2, 3×4 | 6 |

| Command | What |
|---|---|
| `make demo-reset` | Before each rehearsal: migrations, drops every `m_*` schema, empties the registry and guest notices, seeds all but Le Petit Zinc (onboarding builds it live), with La Graine Verte's management data. Refuses a non-localhost `DATABASE_URL`. `SEED="--all --with-bookings"` for a full state |
| `make seed` | The 3 merchants, live, tonight's bookings at Le Petit Zinc and La Graine Verte's management data; idempotent. `SEED="--except le-petit-zinc"` for demo mode |
| `make bookings` | Tonight's bookings at Le Petit Zinc (19:00–21:00, via website and merchant), e.g. after its live onboarding; times already past are skipped |
| `make demo-booking` | The demo's live booking: Kim Weber, 2 guests at 20:00 tonight at La Graine Verte, through the gateway's `create_reservation` (needs `make gateway`), so the merchant gets a real "New booking via Claude"; idempotent, and exits 1 when that booking was cancelled since (`BOOKING="--key 2"` books a new one). `BOOKING="--time 19:30 --url …"` for other values |
| `make outage` / `make restore` | Sets `SLUG` (default `le-petit-zinc`) `down` / `live`: Claude gets the fallback URL while it is down |

La Graine Verte's management data (`--with-management`, #131) is the management mockup's: 55 bookings over the 7 days from the day of the seed (R-001 to R-055, so the live booking through Claude is R-056), the 29 days before made up to the mockup's daily totals, 13:00 blocked tomorrow, and today's 24 requests from Claude in `public.agent_requests`. With a clock at 19:40, Home shows 982 guests over 30 days, 597 through the agent, 42 cancelled, 17 guests in 6 bookings tonight. It goes in once, into a La Graine Verte without bookings: on another day, `make demo-reset` dates it from that day. To open its management site greeted as in the mockup: `make owner-link SLUG=la-graine-verte NAME=Camille`. `tests/fixtures/demo/la-graine-verte-management.json` (also read by the web's mock) and `la-graine-verte-assistant.json` (the assistant's opening conversation) come from the mockup: `uv run scripts/export_management_fixture.py` regenerates them.

## Team server and tools

| Command | What |
|---|---|
| `make deploy` | Builds `web/dist`, then ships the gateway, the BATP node, the platform and the web app to the team server, applies the migrations, installs the systemd units, and updates Caddy when `deploy/Caddyfile` changed ([connector.md](../documentation/runbooks/connector.md), [platform.md](../documentation/runbooks/platform.md)) |
| `make web-dist` | Builds `web/dist`, the web app `make deploy` ships; refuses a mock build (`VITE_MOCK`) |
| `make owner-link SLUG=…` | A new secret link for a merchant's owner (`NAME=…` optional); the old one stops working (#132) |
| `make tunnel` | Exposes a local gateway on `GATEWAY_PUBLIC_URL` through ngrok, when the team server can't be used |
| `make rfc` | Rebuilds the BATP Internet-Draft in `documentation/rfc/`: JSON schemas, `.xml`, `.txt`, `.html`, then idnits (needs Docker and `npx`) |

## Checks

```bash
make test                         # uv run pytest
make lint                         # ruff check, ruff format --check, mypy --strict on every package (as CI runs it)
uv run pytest                     # all tests; `-m "not integration"` skips the ones needing Docker
uv run pytest tests/e2e           # §9 user scenarios through a real MCP client
uv run pytest tests/e2e/test_demo_scenario.py   # the demo's order over HTTP, before each rehearsal (#25)
make test-stack                   # Chromium on the built web app, the platform and the gateway: build, publish, book, block, pause (#153)
uv run scripts/crawl_site.py URL  # what the builder's crawl reads from a website (nothing stored)
uv run scripts/bench_extract.py    # builder extraction, 5 runs on 6 sites (needs OPENAI_API_KEY; `--local` for the 4 local ones)
make fmt                          # autofix lint + format
npx @modelcontextprotocol/inspector   # manual MCP testing against :8001/mcp
cd web && npm test && npm run lint && npm run test:e2e   # the web app: unit, lint, browser (web/README.md)
```

Test markers: `integration` (needs Docker), `llm` (calls the OpenAI API, skipped when `OPENAI_API_KEY` is empty), `browser` (Playwright's Chromium, skipped when it isn't installed). `tests/e2e/test_web_stack.py` also needs `web/dist`: without it, or with one older than `web/src`, a local run skips it, and CI, which builds it, fails. The `llm` tests (the builder on the demo sites, the extraction bench on the 4 local sites, the onboarding conversation) run in the **LLM** workflow on pushes to `main` and by hand (Actions → LLM → Run workflow), with the repository secret `OPENAI_API_KEY`; without it the job is skipped with a notice. About 50 s and a few cents per run. `tests/fixtures/generated/` holds profiles generated from 3 real websites.

Shared tool and SSE contracts, fixtures and checks: [CONTRACTS.md](../documentation/CONTRACTS.md).

## Layout

```
packages/
├── core/               models, DB access, availability rules, errors, settings
├── agent/              AgentService, discovery + provisioning (templates/restaurant.json)
├── gateway/            public MCP server: the user tools, fallback, BATP bridge
├── batp/               BATP library: messages, framing, status codes, client, generic node
├── merchant_node/      the BATP node that serves this backend's merchant agents
├── builder/            crawl (Playwright) + extraction (OpenAI API) -> MerchantProfile
└── merchant_platform/  FastAPI: /chat, /events, the management routes, accounts; the platform assistant
web/                    Studio and the management site (Vite + React + TS)
sites/                  demo restaurant websites: le-petit-zinc (crawl target), 2 one-page sites
migrations/             Alembic (public schema only; merchant schemas come from provisioning)
scripts/                seed.py, demo_reset.py, demo_booking.py (make demo-booking), demo_management.py
                        (La Graine Verte's management data), export_management_fixture.py, owner_link.py
                        (make owner-link), set_status.py (outage switch), caddy_env.py and platform_env.py
                        (make deploy), crawl_site.py, bench_extract.py (+ bench_sites/), batp_schemas.py
                        (make rfc)
deploy/                 systemd units and Caddyfile for make deploy
tests/{unit,integration,e2e,fixtures}/
```

`merchant_platform` is not called `platform`: that name is a Python stdlib module and would shadow it.
