# ADR-0020 — Check accessibility with axe, and take screenshots in Playwright's image

- **Status**: Accepted
- **Date**: 2026-11-01
- **Context issue**: [#153](https://github.com/MaxEntz/BaselHack2026/issues/153), part of [#127](https://github.com/MaxEntz/BaselHack2026/issues/127)

## Context

#153 asks for two things the web app's tests didn't cover:

- **Accessibility:** WCAG AA on every page, including keyboard paths, a focus ring on every stop, labels on icon buttons, 4.5:1 on text and reduced motion.
- **Visual tests:** a screenshot of every page and every Studio state at 1440 and 390 px, checked in CI.

ADR-0017 rejected screenshot diffs against the mockup, because pixels differ from one machine's font rendering to another's. The computed-style comparisons it chose hold each part to the mockup. They don't see a page as a whole: a box that moves, a card that disappears or text that overflows all go through.

A first run of axe-core on the app found real failures, which none of the tests had caught:

- the mockup's olive (`#727f55`) under light text reads at 3.8:1, and the sidebar's band at 4.4:1;
- small titles in olive read at 3.6:1;
- on the floor plan, a table's accessible name didn't hold the words it shows.

## Decision

- **axe-core** (`@axe-core/playwright`, dev dependency), in `e2e/a11y.spec.ts`:
  - WCAG 2.0 to 2.2 A and AA rules on the 9 management pages, Reservations' two dialogs and Studio from the first message to the built site, at 1440 and 390 px;
  - next to it, keyboard tests: a ring on every Tab stop of every page and of Studio; the nav in order; a modal that keeps the focus and gives it back; the search; nothing animating with reduced motion.
- **Screenshots of our own pages, not of the mockup**, in `e2e/visual.spec.ts`:
  - committed in `e2e/__screenshots__/`, taken and checked only inside Playwright's Docker image (`mcr.microsoft.com/playwright:v<version>-noble`);
  - the CI job `visual` runs in that image, and `npm run test:visual:docker` runs it locally;
  - the page's clock is stopped at 19:40 and moved by hand, so the agent's live booking never arrives in a shot.
  - The mockup stays the reference through ADR-0017's style comparisons. The screenshots keep the pages from drifting after that.
- **Titles in the accent, and the text on it, read at 4.5:1 whatever the merchant's colours:**
  - `lookStyle` moves the accent away from the page (darker, or lighter on a dark page) until titles in it read on the page and the cards, and further if no text colour reads on both the accent and its band;
  - the text on the accent follows, and becomes black or white when its own colour never gets there;
  - the second accent stays the platform's to check (#209);
  - `tokens.css` gives La Graine Verte the result (`#616c48`);
  - the comparisons with the mockup give the mockup the same olive (`e2e/mockup.ts`), so every other colour still has to match.

## Alternatives considered

### Hand-written accessibility checks — rejected

Contrast through alpha and stacked backgrounds, name computation and ARIA rules are what axe already implements and keeps current with WCAG. Hand-written checks would cover less and be ours to maintain.

### Screenshots on the CI runner itself — rejected

ADR-0017's reason holds: the runner's fonts and hinting differ from a laptop's. Baselines taken on one would fail on the other. The image fixes the browser build and the system fonts for both.

### Full-page screenshots — rejected

To take a full page, Playwright makes the window as tall as the page. The sidebar and the assistant (`100vh`, sticky) then grow, and two shots of one page differ. Each shot is the 900 px high window.

### Keep the mockup's olive — rejected

Below 4.5:1 for 15 px text, and white text on `#727f55` reaches only 4.3:1. Darker text, or bigger text, would change the design more than a slightly darker olive does.

## Consequences

- Positive: a change that breaks contrast, a name, the focus or a page's layout fails a test, with the diff image as a CI artifact.
- Negative: retaking the baselines needs Docker. The image is about 3.6 GB the first time.
- Negative: any intended visual change (a page, a token, the fixture) retakes the baselines in the same PR.
- Negative: La Graine Verte's management site is slightly darker than its mockup and its public site.
- This choice becomes wrong if: the screenshots fail on unrelated changes often enough that people retake them without looking. Then keep fewer shots, of the pages' stable parts.

## Sources

- [officiel] axe-core rules — https://dequeuniversity.com/rules/axe/ — what each rule checks, for example `color-contrast` and `label-content-name-mismatch`
- [officiel] Playwright, visual comparisons — https://playwright.dev/docs/test-snapshots — `toHaveScreenshot`, and why baselines depend on the host system
- [officiel] Playwright Docker images — https://playwright.dev/docs/docker — the `mcr.microsoft.com/playwright` image per version
- [spec] WCAG 2.2, 1.4.3 Contrast (Minimum) and 2.5.3 Label in Name — https://www.w3.org/TR/WCAG22/
- [code] `code/web/e2e/a11y.spec.ts`, `code/web/e2e/visual.spec.ts`, `code/web/e2e/mockup.ts`, `code/web/scripts/visual.sh`, `code/web/src/management/look.ts`
