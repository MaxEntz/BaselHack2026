# ADR-0016 — Use React Router, Vitest with Testing Library, and Playwright in the web app

- **Status**: Accepted
- **Date**: 2026-10-31
- **Context issue**: [#130](https://github.com/MaxEntz/BaselHack2026/issues/130), part of [#127](https://github.com/MaxEntz/BaselHack2026/issues/127)

## Context

`code/web` was one chat page: no router, and tests only on pure logic, run by Node's own test runner. AGENTS.md §14 (#86) left Vitest "for when components need a DOM". The management site (#127) adds Studio at `/` and nine pages under `/manage/<slug>`, built from the v10 mockups with dialogs, toasts and save bars, and its pages must look the same as the mockups.

## Decision

- **Routes:** `react-router` 7 in data mode (`createBrowserRouter`). `/` is Studio (the current chat until #141), `/manage/:slug` and `/manage/:slug/<page>` are the management pages; an unknown page goes to that merchant's Home, any other unknown path to `/`.
- **Component tests:** Vitest with Testing Library on jsdom, for files `src/**/*.test.{ts,tsx}`. Node's runner keeps the pure-logic tests in `tests/`. `npm test` runs both.
- **Browser tests:** Playwright on Chromium (`e2e/`, `npm run test:e2e`), at 1440 px, against Vite in mock mode. One suite compares each primitive's computed styles with the same markup inside `documentation/design/poc-v10/sesame-management.html`.
- Both run in the CI `web` job.

## Alternatives considered

### Keep hash routing, as the mockup does — rejected

The mockup switches pages on `#reservations`, `#floor`… in one static file. Real paths give each page its own URL that the platform can link to and that a reload keeps, and they leave the hash free for in-page anchors.

### Vitest browser mode instead of jsdom plus Playwright — rejected

It would put the component tests in a real browser too, but each run then needs a browser. jsdom keeps them fast; the few things jsdom can't check (fonts, computed styles against the mockup, `<dialog>` in the top layer) go to Playwright.

### Screenshot diffs against the mockup — rejected

Pixel comparisons break on font hinting and anti-aliasing between machines. Comparing computed styles (sizes, paddings, colours, fonts, shadows) catches the same mistakes and gives a readable diff.

## Consequences

- Positive: pages are tested where they run; the mockup stays the reference automatically.
- Negative: CI downloads Chromium for the `web` job (about 100 MB, cached by the runner image only partly).
- Negative: jsdom has no `<dialog>` methods; `src/test-setup.ts` stands in for `showModal` and `close`.

## Sources

- [code] `code/web/src/router.tsx`, `code/web/vitest.config.ts`, `code/web/playwright.config.ts`, `code/web/e2e/primitives.spec.ts`
- [doc] [React Router: data mode](https://reactrouter.com/start/data/installation), [Vitest](https://vitest.dev/guide/), [Playwright](https://playwright.dev/docs/intro)
