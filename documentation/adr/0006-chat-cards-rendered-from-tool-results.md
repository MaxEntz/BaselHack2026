# ADR-0006 — Render chat cards from tool results, not from the LLM's text

- **Status**: Accepted
- **Date**: 2026-10-31

## Context

The merchant manages everything by chat (AGENTS.md §1). The structured answers are:
- the build steps;
- the agent profile;
- tonight's reservations;
- the stats;
- the policy;
- the notification "new booking via Claude".

The merchant acts on these, for example by cancelling R-001 or blocking 21:00. If the assistant's LLM wrote the reservations table in Markdown, it could drop a row, reorder or round numbers, or invent a booking, and the merchant would trust it.

## Decision

- The platform streams **typed events** over SSE (`ChatEvent` in `core/models.py`, mirrored in `web/src/types.ts`): `text`, `tool_start`, `step`, `card`, `error`, `done`.
- A `card` event carries the **tool's own output** as `data`. For example, `ReservationsCardData` is `ListReservationsOutput` plus the date, and `ProfileCard` carries `BuildAgentOutput`.
- The web app renders each card kind with its own component (`web/src/cards/CardView.tsx`). The LLM's text only comments on the card around it.

## Alternatives considered

### The LLM formats results as Markdown tables — rejected

The table is only as accurate as the generation. It also cannot be interactive or styled per kind (profile, stats), and it costs output tokens.

### The web app calls the admin API directly to draw cards — rejected

That would add a second, form-like path next to the chat (against §1). Each card would also need its own endpoint and state.

## Consequences

- Positive: what the merchant sees is exactly what the service returned. Pydantic and TypeScript check the same shapes, and `npm run check:contracts` compiles the mocks against `ChatEvent`.
- Positive: the web app was built and demoed on mocks (`VITE_MOCK=1`) before the backend existed.
- Negative: two type definitions (Python and TypeScript) must change together (`documentation/CONTRACTS.md`).
- This choice becomes wrong if: answers become open-ended (free-form analysis) rather than tool results. Text is then the right medium.

## Sources

- [code] `documentation/CONTRACTS.md` — event stream format and the mocks
- [code] `code/web/src/cards/CardView.tsx` — one component per card kind
- [spec] WHATWG HTML, server-sent events — https://html.spec.whatwg.org/multipage/server-sent-events.html — `event:` names and framing
