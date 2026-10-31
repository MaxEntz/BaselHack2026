# Architecture decision records

One decision per file, newest first. An accepted ADR is never edited: when it stops being true,
write a new one and mark the old one `Superseded by ADR-NNNN`.

| ADR | Decision | Status | Date |
|---|---|---|---|
| [0011](0011-our-own-agent-protocol-matp-with-mcp-as-bridge.md) | Define our own agent protocol (MATP) on a dedicated port, with MCP as Claude's bridge | Proposed | 2026-10-31 |
| [0010](0010-allow-only-anthropics-outbound-range-on-the-public-gateway.md) | Allow only Anthropic's outbound range on the public gateway | Accepted | 2026-10-31 |
| [0009](0009-serve-the-gateway-from-the-team-server-behind-caddy.md) | Serve the gateway from the team server behind Caddy | Accepted (access restriction superseded by 0010) | 2026-10-31 |
| [0008](0008-deviations-from-the-original-tool-contracts.md) | Deviate from the AGENTS.md §8 tool contracts where they were unsafe or incomplete | Accepted | 2026-10-31 |
| [0007](0007-builder-cache-for-a-replayable-demo.md) | Cache builder results so the demo can be replayed offline | Accepted (implementation: #22) | 2026-10-31 |
| [0006](0006-chat-cards-rendered-from-tool-results.md) | Render chat cards from tool results, not from the LLM's text | Accepted | 2026-10-31 |
| [0005](0005-rename-platform-package-to-merchant-platform.md) | Name the merchant platform package `merchant_platform`, not `platform` | Accepted | 2026-10-31 |
| [0004](0004-openai-for-backend-llm-calls.md) | Use the OpenAI API for backend LLM calls | Accepted | 2026-10-31 |
| [0003](0003-llm-fills-a-schema-never-generates-code.md) | Let the LLM fill a validated schema, never generate code | Accepted | 2026-10-31 |
| [0002](0002-python-mcp-v2-fastapi-postgres-react-stack.md) | Build on Python 3.12, MCP SDK v2, FastAPI, PostgreSQL 16 and Vite/React | Accepted | 2026-10-31 |
| [0001](0001-one-postgres-schema-per-merchant.md) | Isolate each merchant in its own Postgres schema | Accepted | 2026-10-31 |
