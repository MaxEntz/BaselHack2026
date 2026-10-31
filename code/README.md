# Merchant Agents — code

Spec: [`AGENTS.md`](../AGENTS.md) (also loaded by Claude Code through `CLAUDE.md`). Run every command from this `code/` directory.

## Prerequisites

- [uv](https://docs.astral.sh/uv/) (it installs Python 3.12 itself if needed)
- Docker (Postgres 16, and testcontainers for the integration tests)
- Node.js ≥ 22.12 (Vite)
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
| `make gateway` | MCP server (protocol), `uv run python -m gateway`: the 6 user tools, JSON logs, `GET /health` | http://localhost:8001/mcp |
| `make platform` | Platform API, `uv run uvicorn merchant_platform.api:app --port 8002` | http://localhost:8002 |
| `make web` | Merchant chat UI (Vite) | http://localhost:5173 |
| `make site` | Demo restaurant website (static) | http://localhost:8080 |

Claude.ai needs a public HTTPS URL: `make deploy` ships the gateway to the team server (`https://191-96-88-152.sslip.io/mcp`). See [`documentation/runbooks/connector.md`](../documentation/runbooks/connector.md).

## Demo data

Three fictional restaurants in Mulhouse, in `tests/fixtures/` (also the `demo_profiles` pytest fixture):

| Slug | Tags | Price | Services | Tables | Max party |
|---|---|---|---|---|---|
| `le-petit-zinc` | vegetarian, gluten-free, terrace, kid-friendly | €€ | 12:00–14:00, 19:00–22:00 | 4×2, 6×4, 2×6 | 6 |
| `la-graine-verte` | vegan, vegetarian, gluten-free | € | 11:30–14:30, 18:30–21:30 | 6×2, 3×4 | 4 |
| `maison-albane` | vegetarian, terrace | €€€€ | 19:30–22:30 | 5×2, 3×4 | 6 |

| Command | What |
|---|---|
| `make demo-reset` | Before each rehearsal: migrations, drops every `m_*` schema, empties the registry and guest notices, seeds all but Le Petit Zinc (onboarding builds it live). Refuses a non-localhost `DATABASE_URL`. `SEED="--all --with-bookings"` for a full state |
| `make seed` | The 3 merchants, live, plus tonight's bookings at Le Petit Zinc; idempotent. `SEED="--except le-petit-zinc"` for demo mode |
| `make bookings` | Tonight's bookings at Le Petit Zinc (19:00–21:00, via website and merchant), e.g. after its live onboarding; times already past are skipped |
| `make outage` / `make restore` | Sets `SLUG` (default `le-petit-zinc`) `down` / `live`: Claude gets the fallback URL while it is down |

## Checks

```bash
uv run pytest                     # all tests; `-m "not integration"` skips the ones needing Docker
uv run pytest tests/e2e           # §9 user scenarios through a real MCP client
uv run ruff check . && uv run ruff format --check . && uv run mypy packages/core packages/agent packages/gateway
make fmt                          # autofix lint + format
npx @modelcontextprotocol/inspector   # manual MCP testing against :8001/mcp
```

Test markers: `integration` (needs Docker), `llm` (calls the OpenAI API, skipped when `OPENAI_API_KEY` is empty).

Shared tool and SSE contracts, fixtures and checks: [CONTRACTS.md](../documentation/CONTRACTS.md).

## Layout

```
packages/
├── core/               models, DB access, availability rules, errors, settings
├── agent/              AgentService + provisioning (templates/restaurant.json)
├── gateway/            public MCP server: discovery, preference filters, fallback
├── builder/            crawl (Playwright) + extraction (OpenAI API) -> MerchantProfile
└── merchant_platform/  FastAPI /chat + /events, platform assistant
web/                    merchant chat UI (Vite + React + TS)
sites/le-petit-zinc/    demo restaurant website
migrations/             Alembic (public schema only; merchant schemas come from provisioning)
scripts/                seed, demo reset, outage switch
tests/{unit,integration,e2e,fixtures}/
```

`merchant_platform` is not called `platform`: that name is a Python stdlib module and would shadow it.
