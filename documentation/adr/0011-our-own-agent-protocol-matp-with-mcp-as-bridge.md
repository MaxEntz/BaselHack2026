# ADR-0011 — Define our own agent protocol (MATP) on a dedicated port, with MCP as Claude's bridge

- **Status**: Accepted
- **Date**: 2026-10-31

## Context

The team wants Merchant Agents to be **a network reserved for AI agents**, not one more web API.
The AGTP Internet-Draft makes the same argument: agent traffic on HTTP looks like human traffic,
so it can't be identified, routed or governed as agent traffic. AGTP itself is an individual
draft whose method set changed between versions, with no implementation we could find and no
client that speaks it.

Two hard constraints:
- **Claude.ai and Claude Code only speak MCP** (over HTTPS here, ADR-0009 and ADR-0010). The demo, "let Claude book", depends on it.
- The team server is shared. Its firewall only opens ports 22, 80 and 443 inbound (AGENTS.md §0).

## Decision

- We define **MATP** ([`documentation/PROTOCOL.md`](../PROTOCOL.md)). It runs over TCP on its own port, **4710**, which is unassigned at IANA. Frames are newline-delimited JSON. Every session opens with a `HELLO` that identifies the calling agent. Requests carry an **intent** (`DISCOVER`, `AVAILABILITY`, `BOOK`, `AMEND`, `CANCEL`, `RECALL`, `PING`), a merchant and contract-typed params. Responses carry status codes mapped onto AGENTS.md §8.4, with the fallback URL.
- A **MATP node** serves the merchant agents, on top of the existing `AgentService` and discovery.
- The **MCP gateway becomes a bridge**: its 6 tools stay the same for Claude, and its `AgentClient` speaks MATP to the node.
- POC exposure: the node listens on `127.0.0.1:4710`, and only the gateway on the same host reaches it. Opening the port publicly (TLS plus a firewall rule) is a later, separate decision.

## Alternatives considered

### Implement AGTP — rejected

The draft is unstable (the method list changes between versions), it is individual rather than a standard, and nothing speaks it. We keep its idea (agent-only channel, agent identity, intent methods) and stay small.

### JSON over HTTPS on a dedicated path — rejected by the team

Simpler to deploy (Caddy, ports 80/443), but it is again "HTTP with conventions". The team chose a dedicated connection and port to make the agent network a distinct protocol.

### Drop MCP — rejected

Claude.ai can't speak MATP, so "Claude books" would no longer work.

## Consequences

- Positive: agent traffic gets its own channel and port, with the calling agent named on every request. Any agent, not only Claude, can join the network by speaking MATP.
- Positive: `params` and `result` reuse `core/models.py`, so MATP and the MCP tools can't drift apart.
- Negative: one more hop and one more process. The gateway's 2 s budget (#13) now includes the round trip to the node.
- Negative: we maintain a protocol, its client and its server. There is no ecosystem.
- Negative: exposing 4710 beyond localhost needs TLS and a firewall change on a shared server.
- This choice becomes wrong if: a standard agent-to-agent protocol gains real adoption among the clients we need to reach. Then bridge to it, or replace MATP with it, in a new ADR.

## Sources

- [spec] AGTP Internet-Draft (individual, informational) — https://datatracker.ietf.org/doc/html/draft-hood-independent-agtp-09 — the agent-only protocol idea, intent methods, `agtp://`, port 4480
- [official] IANA service names and port numbers — https://www.iana.org/assignments/service-names-port-numbers/service-names-port-numbers.xhtml?search=4710 — 4710 is in the unassigned range 4705–4710
- [code] `documentation/PROTOCOL.md` — the MATP draft; `code/packages/gateway/src/gateway/agent_client.py` — the `AgentClient` interface the bridge implements
