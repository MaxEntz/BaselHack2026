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
| `make gateway` | MCP server (protocol), `uv run python -m gateway`; only `ping` until #11 | http://localhost:8001/mcp |
| `make platform` | Platform API, `uv run uvicorn merchant_platform.api:app --port 8002` | http://localhost:8002 |
| `make web` | Merchant chat UI (Vite) | http://localhost:5173 |
| `make site` | Demo restaurant website "Le Petit Zinc" (static, in French) | http://localhost:8080 |
| `make sites` | The 3 demo websites: Le Petit Zinc, La Graine Verte, Maison Albane | http://localhost:8080, :8081, :8082 |

Le Petit Zinc's booking form posts to `http://localhost:8002/public/le-petit-zinc/reservations` (#15). Open the site as `localhost`, not `127.0.0.1`: the platform accepts the form from that origin only (CORS). Without the platform, the form asks guests to call.

Claude.ai needs a public HTTPS URL: `make deploy` ships the gateway to the team server (`https://191-96-88-152.sslip.io/mcp`). See [`documentation/runbooks/connector.md`](../documentation/runbooks/connector.md).

## Checks

```bash
uv run pytest                     # all tests; `-m "not integration"` skips the ones needing Docker
uv run ruff check . && uv run ruff format --check . && uv run mypy packages/core
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
sites/                  demo restaurant websites: le-petit-zinc (crawl target), 2 one-page sites
migrations/             Alembic (public schema only; merchant schemas come from provisioning)
scripts/                seed, demo reset, outage switch
tests/{unit,integration,e2e,fixtures}/
```

`merchant_platform` is not called `platform`: that name is a Python stdlib module and would shadow it.
