# Merchant web app

Studio, the chat where merchants build, publish and run their sites, with a canvas that shows
them (`AGENTS.md` §1), and the management site next to it (#127, [ADR-0015](../../documentation/adr/0015-give-merchants-a-management-site-next-to-the-chat.md)). Vite + React +
TypeScript, plain CSS. Mockups: [`documentation/design/poc-v10/`](../../documentation/design/poc-v10/README.md).

```sh
npm ci
npm run dev                # http://localhost:5173, /api → http://localhost:8002
VITE_MOCK=1 npm run dev    # no backend: replays src/mocks/*.json
npm run build              # tsc (strict) + vite build
npm run lint               # oxlint: hooks' dependencies, no function over 100 lines in src/ (#220)
npm run knip               # no unused file, export or dependency (#220)
npm run check:contracts    # mocks vs src/types.ts
npm run check:bundle       # after npm run build: no mock, fixture or gallery in dist/ (#220)
npm test                   # tests/*.test.ts on Node's own runner (Node version: ../README.md), then Vitest (src/**/*.test.{ts,tsx})
npx playwright install chromium   # once
npm run test:e2e           # Playwright (e2e/), 1440 px, starts Vite in mock mode on :5179
npm run test:visual:docker # screenshots (e2e/visual.spec.ts) in Playwright's Docker image; `-- --update-snapshots` retakes them
```

Routes ([ADR-0017](../../documentation/adr/0017-router-and-test-tools-for-the-management-site.md)):
`/` is Studio, `/manage/<slug>` and `/manage/<slug>/<page>`
(`assistant`, `reservations`, `guests`, `hours`, `floor`, `rules`, `agent`, `settings`) the
management site. An unknown page goes to that merchant's Home, any other path to `/`. The management
site is its own chunk, loaded on its first visit (`src/management/LazyManagePage.tsx`, #220).

`e2e/primitives.spec.ts` compares each management primitive's computed styles with the v10
mockup (`documentation/design/poc-v10/sesame-management.html`), and `e2e/shell.spec.ts` the shell's
place, look and words at 1440 and 390 px, with the clock at 19:40. `SESAME_MOCKUP` points both at another copy.

`e2e/a11y.spec.ts` runs axe-core's WCAG 2.2 AA rules on every page, the dialogs and Studio at
1440 and 390 px, and checks the keyboard: a focus ring on every stop, the nav, the dialogs, the
search, and nothing moving with reduced motion. `e2e/visual.spec.ts` holds every page and Studio
state to its screenshot in `e2e/__screenshots__/`. Fonts render differently from one system to
another, so these screenshots are taken and checked only in Playwright's image, as the CI job
`visual` does. Retake them in the PR that changes a page on purpose
([ADR-0020](../../documentation/adr/0020-check-accessibility-with-axe-and-screenshots-in-playwrights-image.md)).

Open <http://localhost:5173/?gallery> to review every management primitive and every card and state (steps,
profile, reservations incl. empty, stats, policy, notifications, errors). It exists in development and with
`VITE_MOCK=1` only.

A production build (`npm run build`) holds no mock, demo fixture or gallery, and no demo restaurant unless
`VITE_DEMO=1` (#220): they are read through `import.meta.env.VITE_MOCK` written in full, which Vite replaces
at build time, and loaded with dynamic `import()`. `npm run check:bundle`, after the build (the CI `web` job
runs it), fails if `dist/` holds one of them or any `.jpg`. The mock build inlines every dynamic import
(`vite.config.ts`), so that it ships one chunk and one stylesheet, in the order the screenshots were
taken with: the production build's rules, the management site's before Studio's.

## Environment

| Variable | Effect |
|---|---|
| `VITE_MOCK=1` | Replays the contract mocks with realistic delays instead of calling the API. During onboarding, a description containing the word "cut" drops the stream while the agent is being built (#86). The management client (`src/management/`) then serves the demo fixture, `code/tests/fixtures/demo/la-graine-verte-management.json`, with the server's booking rules (#129). |
| `VITE_MERCHANT_SLUG` | Skips onboarding: the chat starts as this merchant and subscribes to its live events. |
| `VITE_MERCHANT_NAME` | Name shown in the top bar with `VITE_MERCHANT_SLUG` (defaults to the slug). |
| `VITE_DEMO=1` | Offers the three demo restaurants in Studio (always on with `VITE_MOCK=1`). |
| `VITE_DEMO_SITES_DOMAIN` | Their sites' domain, as `<slug>.<domain>`; without it, the `make sites` ports. |

## Mock conversation

Without `VITE_MERCHANT_SLUG`: send a website address and a short description to replay
`onboarding.json` (La Graine Verte, like the management fixture). A "new booking via Claude" notification
(`notification.json`) arrives a few seconds after the agent is live. Then any
message replays `tonight.json`, and messages mentioning *block*, *busy*, *stats*,
*policy* or *party* replay `cards.json`.

## Accounts (#132)

After a build, the profile card shows the owner's private link, once: it opens the management site
in any browser (`/api/login?token=…` sets the owner cookie). When the platform sets
`PLATFORM_INVITE_CODE`, a new build needs the invitation link, `/?invite=<code>`: the Studio sends
the code once and removes it from the address bar.

## Layout

- `src/studio/`: Studio (#141). `StudioPage.tsx` puts the page together from its parts and hooks.
  `phase.ts` holds, as pure functions, what each conversation state
  shows: the phase, the feed, the chips and the top bar's pill. `feed.tsx` draws the feed and its
  cards with the fixed sentences, `Hello.tsx` the empty conversation's question (in the feed's log, so that the first reply is announced), `TopBar.tsx` the
  top bar, `Rail.tsx` the restaurants on the left, `PaneSwitch.tsx` the narrow layout's two panes,
  `Composer.tsx` the chips and the message box (it holds the text typed, so a keystroke redraws only it), `Canvas.tsx` the pasteboard and the public site as read,
  `AgentSheet.tsx` the agent site's frames, `motion.ts` the split's opening (the crossfades are
  `src/management/motion.ts`'s), `publish.ts` the top bar's Publish (the route, with no model in
  the way), `chatClient.ts` the chat's client, the mock's with `VITE_MOCK=1`, and its welcome.
  The hooks: `useCanvasView.ts`, what the canvas shows (view, lit fact, moment, pane in front) and
  the assistant's view cards; `useReviewPlayback.ts`, the review's facts lighting up one by one;
  `useOverview.ts`, what the platform knows of the merchant; `useTurns.ts`, what the messages,
  chips and buttons start; `useCopy.ts`, the copy buttons' toast.
  `src/moments.ts` works out the moment of the day for Home's top and for Studio's dock; add
  `?moment=morning|before|during|after` to `/manage/<slug>` to preview one. Its classes start with `st-`, because `cards.css` is global.
- `src/chat/state.ts`: `ChatItem` union and the pure reducer that turns `ChatEvent`
  and `LiveEvent` into chat items (steps of one build grouped, text deltas merged).
- `src/chat/client.ts`: `POST /api/chat` SSE client and `EventSource` on
  `/api/events?merchant=<slug>`; `src/mocks/mockClient.ts` is its mock twin. `sse.ts` splits
  the stream into frames, `changes.ts` says when a turn changed the merchant's data (#152),
  `chime.ts` plays the live notifications' sound.
- `src/chat/useChat.ts`: wires the client to the reducer.
- `src/management/client.ts`: the management site's calls, one per `/api/merchants/<slug>` route
  (`ManagementClient`, #129); `src/mocks/managementClient.ts` is its mock twin, and
  `src/management/index.ts` picks one of them. The mock puts together the route groups of
  `src/mocks/management/`, which share the one state `context.ts` makes: `reservations.ts`
  (bookings, guests, blocked times), `forms.ts` and `tables.ts` (hours, floor plan, rules,
  settings), `agent.ts` (the merchant, its agent and its requests), `home.ts` (Home's numbers)
  and `live.ts` (the live booking). `bookings.ts` applies the booking rules, `input.ts` reads
  the bodies as the platform does.
- `src/cards/`: rich messages rendered from `step`/`card` events and live
  notifications, never from the assistant's Markdown; `Gallery.tsx` is `/?gallery`.
- `src/router.tsx`, `src/Studio.tsx`: the routes; `src/RouteError.tsx`, what a page that fails to
  draw shows instead.
- `src/clipboard.ts`: the copy buttons' clipboard, and the text to copy by hand without one.
- `src/ime.ts`: `composing`, true while an input method composes, so that its Enter doesn't send.
- `src/management/`: the management site. `tokens.css` (scoped to `.mg`, light only),
  `fonts.css` and `fonts/` (Fraunces Soft, Karla, self-hosted with their OFL licences),
  `ui/` (Pill, Tag, Box, Blob, Under, Doodle, Seed, Icon, Field, Dialog, Toast, SaveBar, Source),
  `pages.ts` (page ids and titles), `PrimitivesGallery.tsx`. `pages/` holds the pages
  (Home, Reservations, Guests, Opening hours, Floor plan, Booking rules, Agent, Settings), styled by `pages.css`;
  they read through `data.ts` (`useClient`, which tests provide, and `useLoad`), save through
  `useSavedForm.tsx` and ask before leaving unsaved changes (`LeaveGuard.tsx`). `problems.ts`
  words the rule a form broke; `DayBar.tsx` picks a day of the next 7 and a service;
  `BlockDialog.tsx` sets a day's blocked times.
- `src/management/ManagePage.tsx` and `shell/` (#137): the frame around every page. The sidebar,
  the top bar (search of pages and bookings, Cmd/Ctrl+K, the platform's clock in the merchant's
  zone, the bell and its popover, the avatar), the footer, the assistant's column (open above
  1240 px, following resizes; the real assistant, #140). Live refresh: `/events` and the site's own changes
  (`changes.ts`: every mutation of the client, and `announceChange(slug)` for the assistant's
  tools) bump `live.ts`'s version; a dialog or `useHoldRefresh` holds the page's version until
  it closes. New content crossfades through the View Transitions API (`motion.ts`), never with
  reduced motion.
- `src/studio/fonts/`: Studio's fonts (Source Sans 3, Source Code Pro, Gloock), self-hosted.
- `src/clock.ts`: the time pages read; tests fix it with `setClock(DEMO_NOW)` (Sat 31 Oct 2026, 19:40).
- `src/strings.ts`: copy that names the guest's assistant (Claude), in one place for #71.
