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
| `make gateway` | MCP server (protocol), `uv run python -m gateway`; stub until #3 / #11 | http://localhost:8001/mcp |
| `make platform` | Platform API, `uv run uvicorn merchant_platform.api:app --port 8002` | http://localhost:8002 |
| `make web` | Merchant chat UI (Vite) | http://localhost:5173 |
| `make site` | Demo restaurant website (static) | http://localhost:8080 |

## Checks

```bash
uv run pytest                     # all tests; `-m "not integration"` skips the ones needing Docker
uv run ruff check . && uv run ruff format --check . && uv run mypy packages/core
make fmt                          # autofix lint + format
npx @modelcontextprotocol/inspector   # manual MCP testing against :8001/mcp
```

Test markers: `integration` (needs Docker), `llm` (calls the OpenAI API, skipped when `OPENAI_API_KEY` is empty).

## Layout

```
packages/
├── core/               models, DB access, availability rules, errors, settings
├── agent/              AgentService + provisioning (templates/restaurant.json)
├── gateway/            public MCP server: discovery, preference filters, fallback
├── builder/            crawl (Playwright) + extraction (Claude API) -> MerchantProfile
└── merchant_platform/  FastAPI /chat + /events, platform assistant
web/                    merchant chat UI (Vite + React + TS)
sites/le-petit-zinc/    demo restaurant website
migrations/             Alembic (public schema only; merchant schemas come from provisioning)
scripts/                seed, demo reset, outage switch
tests/{unit,integration,e2e,fixtures}/
```

`merchant_platform` is not called `platform`: that name is a Python stdlib module and would shadow it.
