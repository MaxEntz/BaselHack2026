# Merchant chat (web)

Single-column chat where merchants onboard and run their booking agent
(`AGENTS.md` §1). Vite + React + TypeScript, plain CSS.

```sh
npm ci
npm run dev                # http://localhost:5173, /api → http://localhost:8002
VITE_MOCK=1 npm run dev    # no backend: replays src/mocks/*.json
npm run build              # tsc (strict) + vite build
npm run lint
npm run check:contracts    # mocks vs src/types.ts
npm test                   # tests/*.test.ts on Node's own runner (Node version: ../README.md), then Vitest (src/**/*.test.tsx)
npx playwright install chromium   # once
npm run test:e2e           # Playwright (e2e/), 1440 px, starts Vite in mock mode on :5179
```

Routes ([ADR-0017](../../documentation/adr/0017-router-and-test-tools-for-the-management-site.md)):
`/` is Studio (today the chat below), `/manage/<slug>` and `/manage/<slug>/<page>`
(`assistant`, `reservations`, `guests`, `hours`, `floor`, `rules`, `agent`, `settings`) the
management site. An unknown page goes to that merchant's Home, any other path to `/`.

`e2e/primitives.spec.ts` compares each management primitive's computed styles with the v10
mockup (`documentation/design/poc-v10/sesame-management.html`); it is skipped while the
mockup isn't in the tree, or point `SESAME_MOCKUP` at a copy.

Open <http://localhost:5173/?gallery> to review every management primitive and every card and state (steps,
profile, reservations incl. empty, stats, policy, notifications, errors).

## Environment

| Variable | Effect |
|---|---|
| `VITE_MOCK=1` | Replays the contract mocks with realistic delays instead of calling the API. During onboarding, a description containing the word "cut" drops the stream while the agent is being built (#86). The management client (`src/management/`) then serves the demo fixture, `code/tests/fixtures/demo/la-graine-verte-management.json`, with the server's booking rules (#129). |
| `VITE_MERCHANT_SLUG` | Skips onboarding: the chat starts as this merchant and subscribes to its live events. |
| `VITE_MERCHANT_NAME` | Name shown in the header with `VITE_MERCHANT_SLUG` (defaults to the slug). |

## Mock conversation

Without `VITE_MERCHANT_SLUG`: send a website address, then a short description to
replay `onboarding.json`. A "new booking via Claude" notification
(`notification.json`) arrives a few seconds after the agent is live. Then any
message replays `tonight.json`, and messages mentioning *block*, *busy*, *stats*,
*policy* or *party* replay `cards.json`.

## Layout

- `src/chat/state.ts`: `ChatItem` union and the pure reducer that turns `ChatEvent`
  and `LiveEvent` into chat items (steps of one build grouped, text deltas merged).
- `src/chat/client.ts`: `POST /api/chat` SSE client and `EventSource` on
  `/api/events?merchant=<slug>`; `src/mocks/mockClient.ts` is its mock twin.
- `src/chat/useChat.ts`: wires the client to the reducer.
- `src/management/client.ts`: the management site's calls, one per `/api/merchants/<slug>` route
  (`ManagementClient`, #129); `src/mocks/managementClient.ts` is its mock twin, and
  `src/management/index.ts` picks one of them.
- `src/components/`: header, message list (auto-scroll), composer.
- `src/cards/`: rich messages rendered from `step`/`card` events and live
  notifications, never from the assistant's Markdown; `Gallery.tsx` is `/?gallery`.
- `src/router.tsx`, `src/Studio.tsx`: the routes.
- `src/management/`: the management site. `tokens.css` (scoped to `.mg`, light only),
  `fonts.css` and `fonts/` (Fraunces Soft, Karla, self-hosted with their OFL licences),
  `ui/` (Pill, Tag, Box, Blob, Under, Doodle, Seed, Icon, Field, Dialog, Toast, SaveBar),
  `pages.ts` (page ids and titles), `PrimitivesGallery.tsx`.
- `src/studio/fonts/`: Studio's fonts (Source Sans 3, Source Code Pro, Gloock), self-hosted.
- `src/clock.ts`: the time pages read; tests fix it with `setClock(DEMO_NOW)` (Sat 31 Oct 2026, 19:40).
- `src/strings.ts`: copy that names the guest's assistant (Claude), in one place for #71.
