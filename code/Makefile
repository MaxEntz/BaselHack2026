.PHONY: setup db migrate gateway platform web site tunnel seed demo-reset test lint fmt

setup: ## .env, Python deps, Chromium for the crawler, web deps
	test -f .env || cp .env.example .env
	uv sync
	uv run playwright install chromium
	cd web && npm ci

db: ## Postgres 16, waits until healthy
	docker compose up -d --wait db

migrate:
	uv run alembic upgrade head

gateway: ## MCP server -> http://localhost:8001/mcp
	uv run python -m gateway

platform: ## Platform API -> http://localhost:8002
	uv run uvicorn merchant_platform.api:app --port 8002

web: ## Merchant chat UI -> http://localhost:5173
	cd web && npm run dev

site: ## Demo restaurant website -> http://localhost:8080
	uv run python -m http.server 8080 -b 127.0.0.1 -d sites/le-petit-zinc

tunnel: ## Public HTTPS URL for the Claude.ai connector
	@echo "tunnel: not configured yet (needs a fixed-domain ngrok or cloudflared tunnel)" >&2; exit 1

seed:
	uv run python scripts/seed.py

demo-reset:
	uv run python scripts/demo_reset.py

test:
	uv run pytest

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy packages/core

fmt:
	uv run ruff check --fix .
	uv run ruff format .
