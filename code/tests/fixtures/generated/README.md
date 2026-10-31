# Profiles generated from real restaurant websites

Built on 2026-10-31 by the real builder: Playwright crawl, `gpt-6.1-terra` with
`BUILDER_REASONING_EFFORT=none`, extraction prompt `v2.3`. No owner description was given, so
every value comes from the website or from a default the builder announced as an assumption.
This is the M4 acceptance in AGENTS.md §11. To regenerate one, onboard the site and copy
`code/.cache/builder/generated/<slug>.json` here.

Onboarding time: 4.8–11.2 s without cache (crawl + extraction + provisioning), 0.02–0.03 s
with a warm cache.

| Site | What the site states | Profile | Assumptions announced |
|---|---|---|---|
| [kronenhalle.com](https://www.kronenhalle.com/) | Zürich. "Unsere Küche steht Ihnen täglich von 12:00 Uhr bis 22:30 Uhr zur Verfügung." No prices, no group limit, no floor plan online. | `12:00–22:30` (last seating 21:30), Zürich with coordinates from its JSON-LD, homepage fallback | group size 8, `€€`, default floor plan |
| [restaurant-kunsthalle.ch](https://www.restaurant-kunsthalle.ch/) | Basel. "Montag bis Samstag 11.30 – 23 Uhr", terrace. Menus not in the crawled text, no group limit. | `11:30–23:00`, `terrace`, homepage fallback | group size 8, `€€`, **closed on Sunday but Sunday bookings stay open** (#73), default floor plan |
| [volkshaus-basel.ch](https://www.volkshaus-basel.ch/) | Basel brasserie. "Reservation: +41 61 690 93 00"; the crawled pages give no hours, prices or group limit. | defaults, homepage fallback | hours 19:00–22:00, group size 8, `€€`, default floor plan |

Known gaps, all announced to the merchant as assumptions: real sites rarely publish a floor
plan or a group limit; Kunsthalle and Volkshaus keep their menus and hours in images or PDFs
the crawler doesn't read.
