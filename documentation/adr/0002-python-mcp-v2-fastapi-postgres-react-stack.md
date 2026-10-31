# ADR-0002 — Build on Python 3.12, MCP SDK v2, FastAPI, PostgreSQL 16 and Vite/React

- **Status**: Accepted ("single chat page" amended by [ADR-0015](0015-give-merchants-a-management-site-next-to-the-chat.md); the stack stands)
- **Date**: 2026-10-31

## Context

A 24-hour hackathon POC with three hard requirements:

- Claude.ai must call our server as a **remote MCP connector**.
- Two bookings must never get the same table at the same time, even under concurrent requests.
- A booking made from Claude must appear in the merchant's chat **within 2 seconds**.

On top of that, the builder must read real restaurant websites (often JavaScript-rendered) and call an LLM. The team works in parallel on 5 Python packages and one web app.

## Decision

| Layer | Choice |
|---|---|
| Language, packaging | Python 3.12, a **uv workspace** in `code/` (packages `core`, `agent`, `gateway`, `builder`, `merchant_platform`) |
| MCP server | Official `mcp` SDK **v2** (`MCPServer`), Streamable HTTP transport |
| Merchant API | FastAPI + Pydantic v2, SSE responses |
| Database | PostgreSQL 16, SQLAlchemy 2 (async, psycopg 3), Alembic |
| Crawl | Playwright (Chromium) |
| Web | Vite + React + TypeScript |
| Tests | pytest, pytest-asyncio, testcontainers (a real Postgres 16) |

## Alternatives considered

### Go or Rust for the gateway — rejected

Their MCP SDKs are less mature than the Python one. A second language would also split the shared Pydantic models (`core.models`) that the gateway, the platform and the builder all use. To be revisited only if latency or load require it.

### MCP SDK v1 (`FastMCP`) — rejected

Spike #3 showed that v2 works from Claude.ai, Claude Code and the SDK client (`documentation/runbooks/connector.md`). In v2, the v1 import path (`mcp.server.fastmcp`) no longer exists, so mixing v1 examples in would break.

### SQLite — rejected

It has no exclusion constraint on time ranges, so double booking would have to be prevented in application code, under concurrency. It also has no `LISTEN/NOTIFY`, so live events would need a broker.

### Redis pub/sub or WebSockets for live events — rejected

Postgres `LISTEN/NOTIFY` already delivers an event on every committed booking, and the merchant UI only needs server → client messages, which SSE provides over plain HTTP.

### Next.js — rejected

No SSR or SEO need: the merchant UI is a single chat page.

### requests + BeautifulSoup for the crawl — rejected

It misses content rendered by JavaScript, which many restaurant sites use.

## Consequences

- Positive: one language for MCP, API, crawl and LLM calls; contracts are shared Pydantic models (`core/models.py`), mirrored once in `web/src/types.ts`.
- Positive: the database itself enforces "no double booking" (see ADR-0001) and produces the live events.
- Negative: the integration tests need Docker (testcontainers). CI runs them on `ubuntu-latest`. On macOS, `tests/integration/conftest.py` fixes the Docker socket path itself.
- Negative: MCP SDK v2 is recent; most examples online are v1 and must not be copied (AGENTS.md §0).
- This choice becomes wrong if: the gateway must serve high traffic with low latency (revisit the language), or live events must reach many browser tabs across several servers (`LISTEN` is per database connection; a broker would then be needed).

## Sources

- [official] MCP Python SDK v2, What's new — https://py.sdk.modelcontextprotocol.io/whats-new/ — `MCPServer` replaces `FastMCP`; migration notes
- [official] MCP specification, transports — https://modelcontextprotocol.io/specification/latest/basic/transports — Streamable HTTP
- [official] FastAPI — https://fastapi.tiangolo.com/ — typed endpoints from Pydantic models
- [official] PostgreSQL 16, constraints — https://www.postgresql.org/docs/16/ddl-constraints.html — `EXCLUDE USING gist`
- [official] PostgreSQL 16, NOTIFY — https://www.postgresql.org/docs/16/sql-notify.html — delivered at commit
- [official] Playwright for Python — https://playwright.dev/python/docs/intro
- [official] Vite — https://vite.dev/guide/
- [official] uv workspaces — https://docs.astral.sh/uv/concepts/projects/workspaces/ — one lockfile for several packages
- [official] testcontainers-python — https://testcontainers-python.readthedocs.io/
