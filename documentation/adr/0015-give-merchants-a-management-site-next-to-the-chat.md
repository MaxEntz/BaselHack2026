# ADR-0015 — Give merchants a management site next to the chat

- **Status**: Accepted
- **Date**: 2026-10-31
- **Supersedes**: the part of [ADR-0006](0006-chat-cards-rendered-from-tool-results.md) that rejects a direct path from the web app to the admin operations (its decision on chat cards stands)
- **Amends**: [ADR-0002](0002-python-mcp-v2-fastapi-postgres-react-stack.md) (one reason for rejecting Next.js; the decision stands)

## Context

AGENTS.md §1 said there is no form-based UI on either side and that the merchant manages everything by chat. ADR-0006 rejected web calls to the admin API because they would add "a second, form-like path next to the chat".

Three things argue against keeping that rule (#127):

- A chat is a poor place for what a merchant looks at all evening: tonight's list, the room, the next arrival. A card scrolls away, and looking again costs a model turn.
- Guests arrive through agents, so their side needs a protocol and no page. The merchant is the one person still looking at a screen.
- The pitch shows a control site that the product didn't have (`documentation/design/poc-v10/PITCH.md`, slide 4). The v10 mockups show what it would be.

## Decision

- **The merchant side has two screens**, both in `code/web`:
  - **Studio**, at `/`: the chat that reads the merchant's website, builds their sites and publishes them.
  - **The management site**, at `/manage/<slug>`: Home, Assistant, Reservations, Guests, Opening hours, Floor plan, Booking rules, Agent and Settings, with ordinary buttons and forms.
- **The chat stays.** It is the way to onboard and to ask for anything, and the management site keeps it as its Assistant.
- **Pages and forms are a second path to the same `AgentService` operations.** Platform routes are thin adapters (AGENTS.md §12). No booking rule lives in a route or in the web app, so a form can't allow what the assistant refuses.
- **Chat cards still come from tool results**, as ADR-0006 decided. Pages render typed route responses, never the LLM's text.
- **A secret link opens a merchant's site** ([ADR-0018](0018-open-a-merchants-site-with-a-secret-link.md)). Proving ownership of the website stays out of scope (AGENTS.md §15).
- **The site is made of fixed components.** Its look is a bounded field that the LLM fills and code checks (#149), so ADR-0003 holds and no page is generated.
- **The guest side doesn't change.** The agent site has no interface, only BATP and the MCP bridge.
- **The stack stays** Vite, React and TypeScript, with a router added ([ADR-0017](0017-router-and-test-tools-for-the-management-site.md)). Next.js stays rejected: the merchant UI is no longer a single chat page, as ADR-0002 said, but there is still no SSR or SEO need, since these pages sit behind a secret link.

This ADR changes no code. The issues of #127 build the site, and some of them landed before it did. Their contract changes go through dedicated PRs (`documentation/CONTRACTS.md`).

## Alternatives considered

### Keep chat only, with more cards — rejected

It keeps one path, but cards still scroll away and each new look still costs a model turn. Slide 4 of the pitch would stay untrue.

### Pages that go through the assistant — rejected

Every click would be a model call: slower and costlier, and able to misread an operation whose inputs are already typed.

### Let the LLM generate each merchant's site — rejected

A crawled website is untrusted input (ADR-0003). Fixed components with a bounded `look` can still take the restaurant's colours and type.

## Consequences

- Positive: a merchant can run an evening from one screen without waiting for a model.
- Negative: there is much more to build and test. #127 estimates about 66 h for the P0 issues, and each operation now has two paths that must agree. Sharing `AgentService` keeps the rules in one place, not the wording or the page state.
- Negative: the platform needs merchant accounts and about 20 page routes, and the web app and the platform must be deployed (#142). Until now, only the gateway and the BATP node were.
- Negative: anyone holding a merchant's secret link can manage that merchant (ADR-0018). That is enough for fictional demo data, not for real restaurants.
- This choice becomes wrong if: merchants do everything through the assistant and the pages go unused. The site could then shrink to what a chat can't do, such as the view during service.

## Sources

- [code] `documentation/design/poc-v10/README.md` — the mockups, and what is real and simulated in them
- [code] `documentation/design/poc-v10/sesame-management.html`, `sesame-studio.html` — the screens this ADR decides to build
- [issue] #127 — order of work, decisions and what to cut; #128 — this decision
