# ADR-0010 — Allow only Anthropic's outbound range on the public gateway

- **Status**: Superseded by [ADR-0019](0019-a-token-for-every-caller-added-by-caddy-for-claude-ai.md)
- **Date**: 2026-10-31
- **Supersedes**: the access-restriction consequence of [ADR-0009](0009-serve-the-gateway-from-the-team-server-behind-caddy.md) ("before the write tools land (#11), restrict access …")

## Context

The gateway is public at `https://191-96-88-152.sslip.io/mcp` (ADR-0009) and open to anyone who knows the URL. With only `ping`, that is harmless. Once #11 deploys `create_reservation`, `update_reservation` and `cancel_reservation`, anyone could book or cancel. Bots already scan the server (`/.env`, `/.git/config`).

`GATEWAY_TOKEN`, a static bearer token, cannot be set from Claude.ai unless the organization has the header-auth beta (`documentation/runbooks/connector.md`). OAuth 2.1 would work from every Claude.ai plan, but it means an authorization server, a consent flow and token storage: too much for a POC whose only legitimate caller is Claude.ai.

Claude.ai calls connectors from Anthropic's cloud, never from the user's browser. Anthropic documents a stable outbound range for "MCP tool calls to external servers": `160.79.104.0/21`. Spike #3's logs showed Claude.ai's calls coming from `160.79.106.x`, inside that range.

## Decision

- In `code/deploy/Caddyfile`, the `191-96-88-152.sslip.io` site reverse-proxies to `127.0.0.1:8001` only for requests whose `remote_ip` is in `160.79.104.0/21`. Every other request gets `403 Forbidden` from Caddy and never reaches uvicorn.
- `remote_ip` (the TCP peer) is the right matcher: nothing sits in front of Caddy. `client_ip` only matters behind another proxy, and it doesn't exist in the server's Caddy 2.6.
- Other site blocks are left untouched.
- The team tests without the public URL: Claude Code or MCP Inspector against a local gateway (`http://localhost:8001/mcp`), or the SDK client run on the server over SSH against `http://127.0.0.1:8001/mcp`. An SSH tunnel (`ssh -L 8001:127.0.0.1:8001 …`) would be simpler, but the server's SSH hardening sets `AllowTcpForwarding no`, and the server is shared.
- `GATEWAY_TOKEN` stays optional, for organizations that do have the header-auth beta. Both checks then apply.

## Alternatives considered

### Static bearer token (`GATEWAY_TOKEN`) — kept optional, not relied on

Already implemented, but Claude.ai can only send it with the header-auth beta, which our accounts don't have.

### OAuth 2.1 — rejected for the POC

The MCP spec's answer and supported by every Claude.ai plan, but it adds an authorization server and a consent flow for a single, known caller.

### Filter in the gateway (ASGI middleware on the client IP) — rejected

It works the same behind a tunnel, but every scan still reaches uvicorn and the Python process. It also has to trust `X-Forwarded-For` correctly. Caddy already sees the real peer address.

### Filter in nftables — rejected

It would block port 443 for every site on the shared server, not only the gateway's host name.

### Turn the gateway off outside tests — rejected

Possible with a tunnel, not with a permanent URL, and the connector would break whenever someone forgets to turn it back on.

## Consequences

- Positive: a request from outside the range gets `403` and leaves no line in `journalctl -u merchant-gateway`. Scans stop at Caddy.
- Positive: no change in Claude.ai's connector settings: it stays on **No sign in**.
- Negative: MCP Inspector, `curl` and Claude Code can no longer use the public URL. They use a local gateway; the deployed one is only reachable from the server itself.
- Negative: anyone who can make Anthropic's infrastructure call our URL still gets through, for example any Claude.ai user who adds the connector. This filters out scanners and direct callers, not other Claude users. Real guest authentication stays out of scope (ADR-0008).
- Negative: the ngrok plan B is not covered. A tunnel URL stays open unless `GATEWAY_TOKEN` is set or the tunnel has its own IP policy.
- This choice becomes wrong if: Anthropic changes its outbound range (the connector then breaks with a `403`; recheck the reference below), or if callers other than Claude.ai must reach the gateway, which would need real authentication (OAuth 2.1).

## Sources

- [official] Anthropic IP addresses — https://platform.claude.com/docs/en/api/ip-addresses — outbound range used "when making MCP tool calls to external servers": `160.79.104.0/21`
- [official] Caddyfile request matchers, `remote_ip` — https://caddyserver.com/docs/caddyfile/matchers#remote-ip
- [official] Caddyfile `handle` directive — https://caddyserver.com/docs/caddyfile/directives/handle
- [code] `code/deploy/Caddyfile`, `documentation/runbooks/connector.md`
- [issue] #45
