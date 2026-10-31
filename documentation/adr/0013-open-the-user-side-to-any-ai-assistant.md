# ADR-0013 — Open the user side to any AI assistant, not only Claude

- **Status**: Accepted
- **Date**: 2026-10-31
- **Extends**: [ADR-0010](0010-allow-only-anthropics-outbound-range-on-the-public-gateway.md), which only let Anthropic's outbound range reach the public gateway

## Context

The project started as "let Claude book", and the code and docs followed. But MCP is an open protocol, and nothing in the 6 tools depends on Claude. Three things did tie the user side to it:

- **Access.** Caddy answered `403` to anything outside Anthropic's range, so Codex, OpenCode or any assistant running on someone's machine could not use the public URL.
- **The record.** Every booking was stored as `via="claude"`, so a booking made from Codex told the merchant "New booking via Claude". The Internet-Draft already called that wrong.
- **The naming.** The gateway's MATP identity (`urn:matp:agent:claude-gateway`), the merchant chat and the docs all said Claude.

## Decision

- **Any MCP client is a first-class user-side client.** Code and docs say "assistant". Claude is named only for what is specific to it: Claude.ai connectors and Anthropic's address range. `CLAUDE.md` and Claude Code stay, as development tools.
- **`Via` becomes `assistant | merchant | website`**, and the merchant sees "New booking via AI assistant". It says an assistant made the booking, not which one.
- **The gateway's MATP agent becomes `urn:matp:agent:mcp-gateway`.**
- **The public URL accepts two kinds of callers.** Hosted assistants that can't send a header pass by network: Anthropic's range for Claude.ai, in the Caddyfile and in `GATEWAY_TOKENLESS_NETWORKS`. Every other caller sends `Authorization: Bearer <GATEWAY_TOKEN>`. Caddy forwards any request with a bearer token, and the gateway checks it.
- **`make deploy` refuses a server without `GATEWAY_TOKEN`,** since Caddy would otherwise forward any token to a gateway that doesn't check it. The gateway refuses to start with `GATEWAY_TOKENLESS_NETWORKS` but no `GATEWAY_TOKEN`.

## Alternatives considered

### Keep the filter, and leave other assistants on a local gateway — rejected

Each would need its own gateway, Postgres and data. They would not book at the merchants on the server, so this is not the same network.

### Open the public URL — rejected

Anyone could then book or cancel, and scans would reach uvicorn again. ADR-0010 still holds for them.

### OAuth 2.1 — rejected for the POC

It would serve every client properly, including hosted ones, but it needs an authorization server and a consent flow. Same reasoning as ADR-0010.

### One token per assistant — rejected for now

It would name the caller in the logs, but `GATEWAY_TOKEN` is a single value today, and the demo doesn't need more.

### Record the assistant's name in `via` — rejected for now

"New booking via Codex" would be better for the merchant. But `clientInfo` is declared by the client, so it can't be trusted, and it needs a free-text column. `assistant` stays accurate.

## Consequences

- Positive: Codex and OpenCode book through the same tools with no change to the tools or their descriptions, and the merchant's chat tells the truth about where a booking came from.
- Negative: one shared secret. Anyone who has it can book or cancel with a guest's name. Rotate it by editing the server's `.env`.
- Negative: scans carrying a fake bearer token reach uvicorn, and get `401` there.
- Negative: changing `Via` changes the `CHECK` constraint of every merchant schema, and merchant schemas have no migrations (ADR-0001), so existing databases must be reset with `make demo-reset`.
- Limit: a hosted assistant from another vendor that can't send a header needs its range added in both places, or OAuth.
- Limit: without a word about booking, some assistants search the web instead of calling the tools. That depends on the client: with the same wording, the Messages API connector used the tools, while Codex and OpenCode did not.
- This choice becomes wrong if: real users book through the protocol. A shared token is not identity, so it would need real authentication (ADR-0008).

## Sources

- [code] `code/deploy/Caddyfile`, `code/packages/gateway/src/gateway/server.py` (`BearerAuth`), `documentation/runbooks/connector.md`
- [spec] `documentation/rfc/draft-merchant-agents-matp-00.md` — the `via` channel
- [official] Anthropic IP addresses — https://platform.claude.com/docs/en/api/ip-addresses
- [official] Caddyfile request matchers — https://caddyserver.com/docs/caddyfile/matchers
