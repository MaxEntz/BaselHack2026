# ADR-0019 — A token for every caller of the public gateway, added by Caddy for Claude.ai

- **Status**: Accepted
- **Date**: 2026-10-31
- **Supersedes**: [ADR-0010](0010-allow-only-anthropics-outbound-range-on-the-public-gateway.md), which let only Anthropic's outbound range reach the public gateway

## Context

ADR-0010 answers `403` to anything outside Anthropic's range (`160.79.104.0/21`), because Claude.ai can't send a bearer token without the header-auth beta. Nothing in the 6 tools depends on Claude, though: Codex 0.165 and OpenCode 2.0.32 booked, moved and cancelled through a local gateway with a token (#71). On the public URL, they get `403`, so they can't book at the merchants on the server.

#71 opened the URL to them with a second rule in the gateway: callers from `GATEWAY_TOKENLESS_NETWORKS` may omit the token. Its review found the flaw: the gateway sees the address uvicorn derives from `X-Forwarded-For`, which it trusts from `127.0.0.1`. Caddy is on loopback, but so is every other process on the shared server: any of them could send `X-Forwarded-For: 160.79.104.7` and skip the token, then book or cancel under any guest name.

## Decision

- **The gateway always requires `GATEWAY_TOKEN`** on the team server, and never trusts a client address. `make deploy` refuses a server whose `code/.env` has no `GATEWAY_TOKEN`. The gateway's code doesn't change: `BearerAuth` already checks every request but `/health`.
- **Caddy adds the token for Anthropic's range.** In the `191-96-88-152.sslip.io` block, requests from `160.79.104.0/21` (Caddy's `remote_ip`, the TCP peer) are forwarded with `Authorization: Bearer {env.GATEWAY_TOKEN}`, replacing whatever they carried. The Claude.ai connector stays on **No sign in**.
- **Any other caller sends the token.** Caddy forwards a request that has `Authorization: Bearer …` and the gateway checks it; any other request gets Caddy's `403`, before uvicorn.
- **Caddy reads the token from `/opt/merchant-agents/caddy.env`**, through a drop-in for its unit (`code/deploy/caddy-merchant-agents.conf`, installed as `/etc/systemd/system/caddy.service.d/merchant-agents.conf`). `make deploy` writes that file from `code/.env` on each deploy (`root:root 600`, read by systemd as root), so the two values can't drift, and restarts Caddy when the file, the drop-in or the Caddyfile changed.

## Alternatives considered

### Networks that may skip the token, checked by the gateway (#71) — rejected

It trusts `X-Forwarded-For` from loopback, which any local process can forge. Fixing it needs Caddy to prove it is Caddy, with a shared secret: the same secret in Caddy's environment as here, plus IP logic and a setting in the gateway that must match the Caddyfile.

### A shared secret header from Caddy (`X-Gateway-Proxy`) — rejected

It works, and a leaked value only helps from the server itself. But it needs a secret in Caddy's environment anyway, and the gateway then keeps its network list. Adding the token is one line of Caddyfile and no code.

### A Unix socket between Caddy and the gateway — rejected for the POC

Only `caddy` and `gateway` could reach uvicorn. But the checks on the server (runbook step 2) call `127.0.0.1:8001`, and how uvicorn handles proxy headers over a socket would need its own tests.

### OAuth 2.1 — rejected for the POC

Same reasoning as ADR-0010: an authorization server and a consent flow for a demo.

## Consequences

- Positive: Codex, OpenCode, Claude Code or any MCP client book at the server's merchants with the token. Claude.ai keeps working with no change to the connector.
- Positive: a forged `X-Forwarded-For`, from outside or from the server, gets nothing without the token. A unit test pins it.
- Negative: the token lives in Caddy's environment, on a shared Caddy: root and the `caddy` user can read it (`/proc/<pid>/environ`). Whoever controls Caddy could already forward anything as Anthropic's range.
- Negative: one shared secret. Anyone who has it can book or cancel under a guest's name. Rotate it by editing `code/.env` on the server and running `make deploy`.
- Negative: scans carrying a fake bearer token reach uvicorn, and get `401` there.
- Negative: anyone with an Anthropic API key can still call from Anthropic's range (the Messages API's MCP connector), as under ADR-0010.
- Limit: bookings from other assistants are still stored as `via = "claude"`, and the merchant reads "New booking via Claude". Renaming `Via` is a contract change (`core/models.py`, `web/src/types.ts`, the management fixture, the `CHECK` of every merchant schema), left to its own PR.
- Limit: a hosted assistant from another vendor that can't send a header needs its range added to the Caddyfile, with the same `header_up`, or OAuth.
- This choice becomes wrong if: real users book through the protocol. A shared token is not identity.

## Sources

- [code] `code/deploy/Caddyfile`, `code/deploy/caddy-merchant-agents.conf`, `code/Makefile` (`deploy`), `code/packages/gateway/src/gateway/server.py` (`BearerAuth`), `code/tests/unit/test_gateway_server.py`
- [review] #71, the thread on `gateway/server.py` (forged `X-Forwarded-For` from loopback)
- [official] Anthropic IP addresses — https://platform.claude.com/docs/en/api/ip-addresses
- [official] Caddy `reverse_proxy` (`header_up`) — https://caddyserver.com/docs/caddyfile/directives/reverse_proxy
- [official] Caddyfile placeholders (`{env.*}`) — https://caddyserver.com/docs/caddyfile/concepts#placeholders
