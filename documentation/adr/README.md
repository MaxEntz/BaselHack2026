# Architecture decision records

One decision per file, newest first. An accepted ADR is never edited: when it stops being true,
write a new one and mark the old one `Superseded by ADR-NNNN`.

| ADR | Decision | Status | Date |
|---|---|---|---|
| [0020](0020-check-accessibility-with-axe-and-screenshots-in-playwrights-image.md) | Check accessibility with axe, and take screenshots in Playwright's image | Accepted | 2026-11-01 |
| [0019](0019-a-token-for-every-caller-added-by-caddy-for-claude-ai.md) | A token for every caller of the public gateway, added by Caddy for Claude.ai | Accepted | 2026-10-31 |
| [0018](0018-open-a-merchants-site-with-a-secret-link.md) | Open a merchant's site with a secret link and a signed cookie | Accepted | 2026-10-31 |
| [0017](0017-router-and-test-tools-for-the-management-site.md) | Use React Router, Vitest with Testing Library, and Playwright in the web app | Accepted | 2026-10-31 |
| [0016](0016-serve-the-web-app-and-the-platform-from-the-team-server.md) | Serve the web app, the platform and the demo sites from the team server, each on its own host | Accepted (the gateway's access: 0019) | 2026-10-31 |
| [0015](0015-give-merchants-a-management-site-next-to-the-chat.md) | Give merchants a management site next to the chat | Accepted | 2026-10-31 |
| [0014](0014-rename-matp-to-batp.md) | Rename our agent protocol from MATP to BATP (Broad Agent Transfer Protocol) | Accepted | 2026-10-31 |
| [0013](0013-store-update-idempotency-keys-in-public.md) | Store the idempotency keys of applied updates in `public`, not in each merchant schema | Accepted | 2026-10-31 |
| [0012](0012-one-mcp-server-for-every-merchant-endpoint.md) | Serve every merchant's own MCP endpoint from one server, routed by the path | Accepted | 2026-10-31 |
| [0011](0011-our-own-agent-protocol-matp-with-mcp-as-bridge.md) | Define our own agent protocol (MATP) on a dedicated port, with MCP as Claude's bridge | Accepted (renamed BATP by 0014) | 2026-10-31 |
| [0010](0010-allow-only-anthropics-outbound-range-on-the-public-gateway.md) | Allow only Anthropic's outbound range on the public gateway | Superseded by 0019 | 2026-10-31 |
| [0009](0009-serve-the-gateway-from-the-team-server-behind-caddy.md) | Serve the gateway from the team server behind Caddy | Accepted (access restriction superseded by 0010, then 0019) | 2026-10-31 |
| [0008](0008-deviations-from-the-original-tool-contracts.md) | Deviate from the AGENTS.md §8 tool contracts where they were unsafe or incomplete | Accepted | 2026-10-31 |
| [0007](0007-builder-cache-for-a-replayable-demo.md) | Cache builder results so the demo can be replayed offline | Accepted (implementation: #22) | 2026-10-31 |
| [0006](0006-chat-cards-rendered-from-tool-results.md) | Render chat cards from tool results, not from the LLM's text | Accepted (rejection of a direct path to the admin operations superseded by 0015) | 2026-10-31 |
| [0005](0005-rename-platform-package-to-merchant-platform.md) | Name the merchant platform package `merchant_platform`, not `platform` | Accepted | 2026-10-31 |
| [0004](0004-openai-for-backend-llm-calls.md) | Use the OpenAI API for backend LLM calls | Accepted | 2026-10-31 |
| [0003](0003-llm-fills-a-schema-never-generates-code.md) | Let the LLM fill a validated schema, never generate code | Accepted | 2026-10-31 |
| [0002](0002-python-mcp-v2-fastapi-postgres-react-stack.md) | Build on Python 3.12, MCP SDK v2, FastAPI, PostgreSQL 16 and Vite/React | Accepted ("single chat page" amended by 0015) | 2026-10-31 |
| [0001](0001-one-postgres-schema-per-merchant.md) | Isolate each merchant in its own Postgres schema | Accepted | 2026-10-31 |
