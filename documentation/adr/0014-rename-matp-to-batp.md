# ADR-0014 — Rename our agent protocol from MATP to BATP (Broad Agent Transfer Protocol)

- **Status**: Accepted
- **Date**: 2026-10-31
- **Amends**: [ADR-0011](0011-our-own-agent-protocol-matp-with-mcp-as-bridge.md) (the protocol's name only; the decision stands)

## Context

ADR-0011 defined our agent-only protocol as **MATP**, the Merchant Agent Transfer Protocol. The same ADR says any agent, not only Claude, can join the network by speaking it. "Merchant" names one side of the exchange and reads as if the protocol were only for merchants' agents (#119).

## Decision

- The protocol is now **BATP, Broad Agent Transfer Protocol**. Everything else in ADR-0011 holds: TCP port 4710, newline-delimited JSON, `HELLO`, intents, status codes, MCP as Claude's bridge.
- The rename is complete, wire included:
  - library `code/packages/batp/` (import `batp`), classes `BatpClient`, `BatpNode`, `BatpRejected`, the gateway's `BatpAgentClient`;
  - HELLO field `"batp"`, URNs `urn:batp:…`, URL scheme `batp://`;
  - settings `BATP_URL`, `BATP_TOKEN`, `BATP_TOKENS`, `BATP_HOST`, `BATP_PORT`, `BATP_TLS_CERT`, `BATP_TLS_KEY`;
  - Internet-Draft `draft-merchant-agents-batp-00` and `documentation/PROTOCOL.md`.
- `make deploy` renames the old keys, URNs and scheme in the team server's `.env`, so the gateway keeps bridging to the node.
- Accepted ADRs keep the name MATP (§12); read it as BATP.

## Alternatives considered

### Rename the documents only, keep the wire and the settings — rejected

Cheaper, but a protocol named BATP that sends `"matp": "0.1"` and `urn:matp:` would confuse anyone implementing the draft. Nothing outside this repository speaks the protocol yet, so the wire can still change for free.

## Consequences

- Positive: the name matches the intent of ADR-0011, an open agent network.
- Negative: a MATP peer can't talk to a BATP node (`HELLO` without `batp` is refused). Only our own gateway and node exist, and they ship together.
- Negative: a teammate's local `.env` with `MATP_*` keys stops bridging silently: the gateway falls back to the in-process agent. `.env.example` shows the new keys.

## Sources

- [code] `documentation/PROTOCOL.md` — the BATP draft; `code/packages/batp/` — library; `code/Makefile` (`deploy`) — the `.env` rename on the team server
