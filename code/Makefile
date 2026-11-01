-include .env

DEPLOY_HOST ?= root@191.96.88.152
DEPLOY_SSH ?= ssh -i ~/.ssh/baselhack2026_lxc -o IdentitiesOnly=yes
DEPLOY_DIR := /opt/merchant-agents/code
PLATFORM_ENV := /opt/merchant-agents/platform/.env

.PHONY: setup db migrate gateway node platform web web-dist site sites tunnel deploy seed demo-reset owner-link test test-stack lint fmt rfc

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

node: ## BATP node (our agent protocol) -> batp://127.0.0.1:4710
	uv run python -m merchant_node

platform: ## Platform API -> http://localhost:8002
	uv run uvicorn merchant_platform.api:app --port 8002

web: ## Merchant chat UI -> http://localhost:5173
	cd web && npm run dev

site: ## Demo restaurant website -> http://localhost:8080
	@echo "Open http://localhost:8080 (localhost, not 127.0.0.1: the booking form's CORS origin)"
	uv run python -m http.server 8080 -b 127.0.0.1 -d sites/le-petit-zinc

sites: ## The 3 demo websites -> http://localhost:8080 (Le Petit Zinc), :8081, :8082
	@echo "Open http://localhost:8080, :8081, :8082 (localhost, not 127.0.0.1: the booking form's CORS origin)"
	@trap 'kill $$pids 2>/dev/null' INT TERM EXIT; \
	uv run python -m http.server 8081 -b 127.0.0.1 -d sites/la-graine-verte & pids="$$!"; \
	uv run python -m http.server 8082 -b 127.0.0.1 -d sites/maison-albane & pids="$$pids $$!"; \
	uv run python -m http.server 8080 -b 127.0.0.1 -d sites/le-petit-zinc

tunnel: ## Expose a local gateway through ngrok (the team URL is served by `make deploy`)
	@test -n "$(GATEWAY_PUBLIC_URL)" || (echo "GATEWAY_PUBLIC_URL is empty: see documentation/runbooks/connector.md" >&2; exit 1)
	ngrok http --url="$(GATEWAY_PUBLIC_URL)" 8001

web-dist: ## Build the merchant web app (web/dist) for make deploy
	@test -z "$$VITE_MOCK" || { echo "VITE_MOCK is set: web-dist would build the mock app" >&2; exit 1; }
	@! grep -sEq '^[[:space:]]*(export[[:space:]]+)?VITE_MOCK[[:space:]]*=[[:space:]]*[^[:space:]#]' web/.env web/.env.local web/.env.production web/.env.production.local || { echo "VITE_MOCK is set in a web/.env* file: web-dist would build the mock app" >&2; exit 1; }
	test -d web/node_modules || (cd web && npm ci)
	cd web && npm run build

deploy: web-dist ## Ship the gateway, the BATP node, the platform and the web app to the team server
	$(DEPLOY_SSH) $(DEPLOY_HOST) 'test -f $(PLATFORM_ENV) || { echo "$(PLATFORM_ENV) is missing on the server (documentation/runbooks/platform.md, step 2)" >&2; exit 1; }'
	rsync -rlptz --chmod=a+rX --delete --exclude .venv --exclude '.env*' --exclude __pycache__ --exclude .pytest_cache --exclude .mypy_cache --exclude .ruff_cache --exclude .hypothesis --exclude '.coverage*' --exclude htmlcov --exclude .cache --exclude web -e "$(DEPLOY_SSH)" ./ $(DEPLOY_HOST):$(DEPLOY_DIR)/
	$(DEPLOY_SSH) $(DEPLOY_HOST) 'mkdir -p $(DEPLOY_DIR)/web/dist'
	rsync -rlptz --chmod=a+rX --delete -e "$(DEPLOY_SSH)" web/dist/ $(DEPLOY_HOST):$(DEPLOY_DIR)/web/dist/
	$(DEPLOY_SSH) $(DEPLOY_HOST) '\
		cd $(DEPLOY_DIR) && \
		{ id -u sesame >/dev/null 2>&1 || useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin sesame; } && \
		chown root:gateway .env && chmod 640 .env && \
		chown -R root:sesame ../platform && chmod 750 ../platform && chmod 640 ../platform/.env && \
		/opt/uv/bin/uv sync --frozen --no-dev --package gateway --package merchant_node --package merchant-platform --package builder -q && \
		( cd ../platform && ../code/.venv/bin/python ../code/scripts/platform_env.py ) && \
		{ ( umask 077 && .venv/bin/python scripts/caddy_env.py > ../caddy.env.new ) || { rm -f ../caddy.env.new; exit 1; }; } && \
		{ cmp -s deploy/Caddyfile /etc/caddy/Caddyfile || caddy validate --adapter caddyfile --config deploy/Caddyfile; } && \
		caddy_changed=0 && \
		if cmp -s ../caddy.env.new ../caddy.env; then rm ../caddy.env.new; else mv ../caddy.env.new ../caddy.env && caddy_changed=1; fi && \
		if ! cmp -s deploy/caddy-merchant-agents.conf /etc/systemd/system/caddy.service.d/merchant-agents.conf; then \
			install -D -m 644 deploy/caddy-merchant-agents.conf /etc/systemd/system/caddy.service.d/merchant-agents.conf && \
			systemctl daemon-reload && caddy_changed=1; \
		fi && \
		if ! cmp -s deploy/Caddyfile /etc/caddy/Caddyfile; then \
			install -m 644 deploy/Caddyfile /etc/caddy/Caddyfile && caddy_changed=1; \
		fi && \
		if [ $$caddy_changed = 1 ]; then systemctl restart caddy; fi && \
		systemctl enable --now --quiet caddy && \
		PLAYWRIGHT_BROWSERS_PATH=/opt/merchant-agents/ms-playwright .venv/bin/playwright install chromium && \
		install -d -o sesame -g sesame .cache && \
		.venv/bin/alembic upgrade head && \
		install -m 644 deploy/merchant-node.service deploy/merchant-gateway.service deploy/merchant-platform.service /etc/systemd/system/ && \
		systemctl daemon-reload && \
		systemctl enable --quiet merchant-node merchant-gateway merchant-platform && \
		systemctl restart merchant-node merchant-gateway merchant-platform'

.PHONY: bookings demo-booking outage restore

SLUG ?= le-petit-zinc

seed: ## The 3 demo merchants, tonight's bookings, La Graine Verte's management data (or SEED=…)
	uv run python scripts/seed.py $(or $(SEED),--all --with-bookings --with-management)

demo-reset: ## Known demo state: all but Le Petit Zinc, built live by onboarding, + management data
	uv run python scripts/demo_reset.py $(SEED)

bookings: ## Tonight's bookings at Le Petit Zinc, e.g. after its live onboarding
	uv run python scripts/seed.py --with-bookings

demo-booking: ## The demo's live booking: Kim Weber, 20:00 for 2 at La Graine Verte, via the gateway
	uv run python scripts/demo_booking.py $(BOOKING)

outage: ## Demo outage: SLUG goes down, Claude gets its fallback URL (SLUG=le-petit-zinc)
	uv run python scripts/set_status.py $(SLUG) down

restore: ## End of the outage: SLUG is live again
	uv run python scripts/set_status.py $(SLUG) live

owner-link: ## A new secret link for SLUG's owner (NAME="Léa" optional); the old one stops working
	uv run python scripts/owner_link.py $(SLUG) $(if $(NAME),--name "$(NAME)")

test:
	uv run pytest

test-stack: web-dist ## The browser test of the whole stack on a fresh build of the web app (#153)
	uv run pytest tests/e2e/test_web_stack.py

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy packages/core packages/agent packages/gateway packages/batp packages/merchant_node

fmt:
	uv run ruff check --fix .
	uv run ruff format .

RFC_DIR := ../documentation/rfc
RFC := draft-merchant-agents-batp-00
XML2RFC := uvx --from xml2rfc==3.35.0 xml2rfc --quiet

rfc: ## BATP Internet-Draft: schemas, .xml, .txt, .html, then idnits (needs Docker and npx)
	uv run python scripts/batp_schemas.py
	docker run --rm -v "$(abspath $(RFC_DIR))":/rfc -w /rfc ruby:3.3-slim sh -c '\
		gem install --no-document --quiet kramdown-rfc -v 1.7.43 >/dev/null && \
		kramdown-rfc --v3 $(RFC).md > $(RFC).v2.xml'
	cd $(RFC_DIR) && $(XML2RFC) --v2v3 $(RFC).v2.xml -o $(RFC).xml && rm $(RFC).v2.xml
	cd $(RFC_DIR) && $(XML2RFC) --text --html $(RFC).xml
	cd $(RFC_DIR) && npx --yes @ietf-tools/idnits@3.3.0 $(RFC).xml
