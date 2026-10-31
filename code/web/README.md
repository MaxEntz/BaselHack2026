# Merchant web app

Today, a single-column chat where merchants onboard and run their booking agent
(`AGENTS.md` §1). Vite + React + TypeScript, plain CSS.

The Sésame site replaces this page with Studio at `/` and adds a management site at
`/manage/<slug>`, next to the chat (#127, [ADR-0015](../../documentation/adr/0015-give-merchants-a-management-site-next-to-the-chat.md)). Mockups:
[`documentation/design/poc-v10/`](../../documentation/design/poc-v10/README.md).

```sh
npm ci
npm run dev                # http://localhost:5173, /api → http://localhost:8002
VITE_MOCK=1 npm run dev    # no backend: replays src/mocks/*.json
npm run build              # tsc (strict) + vite build
npm run lint
npm run check:contracts    # mocks vs src/types.ts
npm test                   # tests/*.test.ts (the chat reducer), Node's own runner (Node version: ../README.md)
```

Open <http://localhost:5173/?gallery> to review every card and state (steps,
profile, reservations incl. empty, stats, policy, notifications, errors).

## Environment

| Variable | Effect |
|---|---|
| `VITE_MOCK=1` | Replays the contract mocks with realistic delays instead of calling the API. During onboarding, a description containing the word "cut" drops the stream while the agent is being built (#86). |
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
- `src/components/`: header, message list (auto-scroll), composer.
- `src/cards/`: rich messages rendered from `step`/`card` events and live
  notifications, never from the assistant's Markdown; `Gallery.tsx` is `/?gallery`.
