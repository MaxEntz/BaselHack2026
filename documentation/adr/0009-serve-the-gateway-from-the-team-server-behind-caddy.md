# ADR-0009 — Serve the gateway from the team server behind Caddy

- **Status**: Accepted
- **Date**: 2026-10-31

## Context

Claude.ai calls custom connectors **from Anthropic's cloud**, not from the user's browser, so `localhost` is unreachable. The gateway needs a public HTTPS URL that stays the same: a connector has to be recreated whenever its URL changes. The demo must not depend on a laptop or on the hackathon Wi-Fi.

The MCP SDK v2 transport rejects any `Host` that is not explicitly allowed, answering 421 (DNS-rebinding protection). Behind a TLS proxy, it must also trust the forwarded headers.

## Decision

- The gateway runs on the team's LXC server (`191.96.88.152`) as the systemd service `merchant-gateway`, bound to `127.0.0.1:8001`.
- Caddy terminates HTTPS with a Let's Encrypt certificate for `191-96-88-152.sslip.io` and reverse-proxies to it.
- `GATEWAY_PUBLIC_URL` sets the allowed host. uvicorn trusts `X-Forwarded-*` only from `127.0.0.1`.
- `make deploy` syncs `code/` and restarts the service.
- An ngrok tunnel (`make tunnel`) remains the documented alternative.

## Alternatives considered

### Tunnel from a laptop (ngrok, cloudflared) — kept as plan B

It needs no server, but the URL lives as long as the laptop and its network do. A free fixed domain helps, but the demo would still depend on the Wi-Fi.

### Bind the gateway on `0.0.0.0` — rejected

Only Caddy needs to reach the gateway. Exposing it directly would bypass TLS and the proxy.

## Consequences

- Positive: a fixed URL, validated from Claude.ai, Claude Code and the SDK client, and still the same after a reboot (spike #3).
- Negative: a change only reaches Claude.ai once deployed. The server is shared, so deploys and Caddy changes need care (AGENTS.md §0).
- Negative: the URL is public, and `GATEWAY_TOKEN` (a static bearer) cannot be set from Claude.ai unless the organization has the header-auth beta. Before the write tools land (#11), restrict access, for example by limiting Caddy to Anthropic's address range `160.79.104.0/21`.
- Negative: `sslip.io` is a third-party DNS service. A real domain would be safer for the demo.
- This choice becomes wrong if: the server becomes unavailable, in which case use the tunnel plan B. Or if the product needs several instances, which would require a real domain, a load balancer and stateless sessions.

## Sources

- [code] `documentation/runbooks/connector.md` — steps, errors, validation table
- [official] Claude, custom connectors with remote MCP — https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp — reachable from Anthropic's IPs, authentication options
- [official] MCP Python SDK v2, deploy — https://py.sdk.modelcontextprotocol.io/run/deploy/ — `allowed_hosts`, 421, proxy headers
- [official] Caddy, `reverse_proxy` — https://caddyserver.com/docs/caddyfile/directives/reverse_proxy
