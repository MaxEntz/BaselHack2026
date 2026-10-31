# ADR-0008 — Deviate from the AGENTS.md §8 tool contracts where they were unsafe or incomplete

- **Status**: Accepted
- **Date**: 2026-10-31

## Context

AGENTS.md §8 lists the tool contracts as first written. When the contracts were frozen (#2), three problems showed up in the demo scenarios (§9):

- A guest could cancel or move **anyone's** booking by guessing `R-001`, because the user-side `update_reservation` and `cancel_reservation` took only a reservation ID.
- When preference filters match nothing ("vegan with terrace"), Claude had nothing to propose, although §9 expects it to offer to drop a filter.
- Onboarding needs to correct an assumed floor plan by chat ("actually we have 8 tables of 2"), and no admin tool did that.

## Decision

The frozen contracts (`core/models.py`, `agent/service.py`, `web/src/types.ts`) change §8 as follows:

- User-side `update_reservation` and `cancel_reservation` require `guest_name`, checked against the booking.
- `search_merchants` also returns `suggestions` (`{drop, matches}`: which filter to drop and how many merchants it would add) and `ignored_filters` (unknown tags, reported rather than rejected). Each merchant also carries `website`.
- `Reservation` also carries `merchant`, `guest_name` and `via`.
- New admin tool `update_tables`. The admin `cancel_reservation` input has no `by`, because the tool is merchant-only and the service receives `by="merchant"`.
- `BuildAgentOutput` adds `assumptions`, `tools` and `mcp_url` for the profile card.
- A new table, `public.guest_notices`, stores merchant cancellations until the guest's next `get_my_reservations`.
- Any later contract change needs a dedicated PR and a message to the team.

## Alternatives considered

### Keep §8 as written — rejected

The cancel and update tools would let anyone cancel any booking. Without suggestions, Claude has to guess why a search came back empty.

### Real guest authentication (accounts, OAuth) — rejected for the POC

It is out of scope (§13: guest identity is the `guest_name` Claude passes). `guest_name` is a weak check, but it is enough to stop blind guessing in a demo.

## Consequences

- Positive: the §9 scenarios can be played as written, including filter relaxation and merchant cancellation with a guest notice.
- Negative: AGENTS.md §8 no longer matches the code. §0 lists the differences and takes precedence; `documentation/CONTRACTS.md` is the reference.
- This choice becomes wrong if: real users book through the protocol. `guest_name` is not identity, so it would need real authentication.

## Sources

- [code] `documentation/CONTRACTS.md` — the frozen contracts and their checks
- [code] PR #28 — https://github.com/MaxEntz/BaselHack2026/pull/28 — contract freeze and review
- [code] AGENTS.md §0 and §14 — corrections and decision log
