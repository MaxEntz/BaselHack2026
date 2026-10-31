# ADR-0012 — Serve every merchant's own MCP endpoint from one server, routed by the path

- **Status**: Accepted
- **Date**: 2026-10-31

## Context

The shared protocol (`/mcp`) needs `search_merchants` and a `merchant` argument on every tool. Issue #23 adds a dedicated endpoint per merchant, `/m/<slug>/mcp`, with only that merchant's booking tools, no `merchant` argument, and descriptions instantiated from its profile. A merchant can add it to Claude directly or give it to a partner. It must:

- work for a merchant onboarded after the gateway started, without a restart;
- keep the shared protocol's safeguards (timeout, `down` status, fallback URL, log line);
- work over MATP as well as in-process (#61).

The MCP SDK v2 binds tools and their descriptions to an `MCPServer` instance, and each instance's session manager must run inside the app's lifespan.

## Decision

- **One second `MCPServer`** serves every merchant endpoint. Its route is `/m/{slug}/mcp` in the gateway's Starlette app, and its session manager runs in the gateway's lifespan next to the shared one.
- **The tools read the slug from the HTTP request** attached to each MCP message (`ctx.request_context.request.path_params`). They call `fallback.call_merchant`, like the shared tools, through the same `AgentClient`.
- **A middleware rewrites `tools/list`**: each description is prefixed with the merchant's sentence from `templates/restaurant.json` (`generated_tools(profile)`), read from the registry at each listing.
- **Merchant free text is kept out of the descriptions.** Clients treat tool descriptions as the connector's instructions, a stronger position than tool results, where the shared `/mcp` keeps merchant data. The profile's free text comes from a crawled website, and anyone can onboard any URL. So the cancellation policy is replaced by "see the restaurant's own website", and the name and city are cut to one line of at most 80 characters, without control characters. Services and party size are validated values (`HH:MM`, integers).
- **An ASGI wrapper answers `404`** before MCP for an unknown slug, a malformed one, or a `building` merchant. A `down` merchant keeps its endpoint and gets the fallback URL from its tools.
- The profile card derives the URL from `mcp_url` (`/mcp` → `/m/<slug>/mcp`), so the frozen `BuildAgentOutput` contract doesn't change.

## Alternatives considered

### One `MCPServer` per live merchant, behind an ASGI dispatcher — rejected

It was the issue's first lead. Each new merchant would need a server created at `deploy` time, in a process that doesn't see `deploy` (the builder runs in the platform), so the gateway would have to poll or listen for registry changes. Each instance's session manager would have to be started and stopped from a background task, since entering a task group from a request task and leaving it in another breaks anyio. Descriptions would also go stale after `update_policy` unless the instance were rebuilt.

### A single low-level server with `list_tools` per slug — close to the decision

That is what the decision does, but on top of `MCPServer`: the tools keep their typed arguments, the same `RejectedArguments` middleware and output schemas as the shared tools.

### Add a `merchant_mcp_url` field to `BuildAgentOutput` — rejected

The contracts are frozen (AGENTS.md §14) and the URL follows from `mcp_url` and the slug.

## Consequences

- Positive: a merchant is reachable at `/m/<slug>/mcp` as soon as `deploy` sets it `live`, and its descriptions follow its policy at the next `tools/list`. Tested in-process and over MATP, with the SDK client and with a session client on the older protocol (`initialize`).
- Positive: Caddy's IP filter (ADR-0010) and `GATEWAY_TOKEN` cover these paths too.
- Negative: each request to `/m/<slug>/mcp` reads the registry once more (the 404 check), and each `tools/list` reads the profile. That is fine for the POC's handful of merchants.
- Negative: a session opened on one merchant's path, then reused on another's, answers for the path of each request. Harmless, since each path only acts on its own merchant.
- Negative: the middleware relies on the SDK's middleware API, which the SDK calls provisional. The e2e tests pin the descriptions.
- Negative: the server's `instructions` are generic. Only the tool descriptions name the merchant.
- Negative: 80 characters of a name can still carry a short instruction. The bound limits tool-description injection, it doesn't remove it; real merchant verification is out of scope (AGENTS.md §15). Claude doesn't learn the cancellation policy from this endpoint.
- This choice becomes wrong if: merchants need different tool sets (other verticals), or the SDK lets an instance's tools change at runtime with `tools/list_changed`.

## Sources

- [official] MCP Python SDK v2, add to an existing app — https://py.sdk.modelcontextprotocol.io/run/asgi/
- [code] `code/packages/gateway/src/gateway/merchant_server.py`, `code/tests/e2e/test_merchant_endpoint.py`
- [issue] #23
