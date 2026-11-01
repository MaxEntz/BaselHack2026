# ADR-0016 — Serve the web app, the platform and the demo sites from the team server, each on its own host

- **Status**: Accepted (deployed by #142; the gateway's access: [ADR-0019](0019-a-token-for-every-caller-added-by-caddy-for-claude-ai.md))
- **Date**: 2026-10-31


## Context

Only the gateway and the BATP node run on the team server (ADR-0009, #62). The merchant side (the web app and the platform API) ran on a laptop, and `ARCHITECTURE.md` still listed the demo topology as an open question (#25). The demo must run from any browser, including a phone on a mobile network (#142): a merchant builds its agent from a website, publishes it, and watches a booking made from Claude arrive live.

Constraints:
- The gateway's public host answers `403` to everyone but Anthropic's outbound range (ADR-0010), so a merchant's browser can't use it.
- The server is shared: other sites must be left untouched, and `make deploy` overwrites `/etc/caddy/Caddyfile` when it changes.
- Live events need the platform and the gateway on the same Postgres (`LISTEN/NOTIFY`), which runs on the server, on localhost only.
- The firewall only opens 22, 80 and 443 inbound.
- The crawler loads an address a merchant typed, on a shared machine: `CRAWL_ALLOW_PRIVATE` must stay off.
- The demo websites (`code/sites/`) ran on `localhost`: neither the server's crawler nor a guest's browser could reach them.

## Decision

- **Hosts**, all `sslip.io` names of the server's address, each a Caddy site block with its own Let's Encrypt certificate:

  | Host | Serves |
  |---|---|
  | `191-96-88-152.sslip.io` | The gateway, unchanged: Anthropic's range only (ADR-0010) |
  | `sesame.191-96-88-152.sslip.io` | The web app (`web/dist`, built on the deploying machine, every unknown path → `index.html`); `/api/*` → the platform on `127.0.0.1:8002`, prefix stripped, `flush_interval -1` for SSE |
  | `le-petit-zinc.…`, `la-graine-verte.…`, `maison-albane.…` | The fictional demo websites, each on its own host like a real restaurant's |

- **The platform** runs as the systemd service `merchant-platform` (`uvicorn merchant_platform.api:app`, one worker, `127.0.0.1:8002`), with the gateway's hardening, plus a writable `.cache` for the builder (ADR-0007).
- **Its own user and settings.** It runs as the system user `sesame`, not `gateway`, from `/opt/merchant-agents/platform`, whose `.env` (`root:sesame`, `640`) holds only what the platform needs (database, OpenAI key, its own settings). `sesame` can't read `code/.env` (`root:gateway`, `640`), so it never sees `BATP_TOKENS` or `GATEWAY_TOKEN`. Reason: Chromium runs without its sandbox (Playwright's default, and Ubuntu 24.04 restricts the unprivileged user namespaces the sandbox needs), so a crawled page exploiting a renderer bug would run as the platform's user.
- **The crawler only reads ports 80 and 443** without `CRAWL_ALLOW_PRIVATE` (`builder/crawl.py`, `WEB_PORTS`). The server's own public address is a global address, so the SSRF guard let a build load `http://191.96.88.152:<port>/`, which reaches the server's services over loopback, even those the firewall hides from the internet. Restaurant sites, the demo ones included, are on 80 and 443. The DNS verdict stays cached per host; the port is checked on every URL and every redirect hop.
- **Chromium** for the crawler is installed by `make deploy` under `/opt/merchant-agents/ms-playwright` (`PLAYWRIGHT_BROWSERS_PATH`). Its system libraries are a one-time `apt` install, outside `/opt`, done only with the team's agreement (`documentation/runbooks/platform.md`).
- **The management address** is `https://sesame.191-96-88-152.sslip.io/manage/<slug>` (#127 decision 8): the mockup's `<slug>.sesame.example` is invented.
- **The demo booking form** posts to `https://sesame.<server>/api/public/le-petit-zinc/reservations` when it is served from the server, and to `http://localhost:8002` locally. The server's `PUBLIC_CORS_ORIGINS` allows Le Petit Zinc's host.
- **Deploy gate:** `make deploy` refuses to go on unless `/opt/merchant-agents/platform/.env` exists and sets `PLATFORM_DEV_ROUTES=0`, #132's `PLATFORM_INVITE_CODE` and `PLATFORM_COOKIE_SECRET`, and an `https://sesame.…` `PLATFORM_WEB_URL`, and doesn't set `PLATFORM_MERCHANT`. Without the invite code anyone could start a build (an OpenAI call and a crawl from the shared server); without the cookie secret every deploy would log every owner out.
- **Secret links through Caddy:** the `sesame.` host sends `Referrer-Policy: no-referrer`, so an owner's `/api/login?token=…` never leaves in a `Referer`, and it has no access log (uvicorn's already masks `token=`, #132).

## Alternatives considered

### Serve the web app on the gateway's host, under a path — rejected

The gateway's host only lets Anthropic's range through (ADR-0010); exceptions per path would weaken that filter and make it harder to read.

### Serve the demo sites under paths of the Sésame host (`/sites/<name>/`) — rejected

The booking form would be same-origin (no CORS), but a restaurant's website would be a sub-path of our own product's address, and the fallback links guests get should look like the restaurant's own site. `sslip.io` gives any number of hosts for free.

### Keep the merchant side on a laptop, with an SSH tunnel to Postgres — rejected

Server SSH port forwarding is disabled (`AllowTcpForwarding no`), and the demo would depend on a laptop and the hackathon Wi-Fi again (ADR-0009's reason).

### Caddy `basic_auth` on the Sésame host until #132 — not chosen

It would allow an earlier deploy, but #132 brings the real access rule (a secret link per merchant). Two auth layers in the demo would confuse the flow.

## Consequences

- Positive: the whole demo runs from any browser; the seeded or onboarded merchants' fallback links and the crawl reach real HTTPS sites.
- Negative: Chromium still runs without its sandbox. A renderer exploit on a crawled page gets the `sesame` user, which can read the platform's settings (the database credentials, shared with the gateway, and the OpenAI key) but not the gateway's or the node's tokens.
- Negative: four more certificates and site blocks in a Caddyfile shared with other sites; `make deploy` now needs Node to build the web app, and restarts the platform on every deploy (in-memory chat sessions are lost).
- Negative: the seeded fixtures still point to `http://localhost:8080…`. A merchant built from `https://le-petit-zinc.…` gets the right links; seeded ones need new fixtures or a re-seed with the server's addresses (#25).
- Negative: `sslip.io` is a third-party DNS service (ADR-0009's limit), now for five hosts.
- This choice becomes wrong if: the product needs its own domain, or the platform needs more than one worker (sessions would have to leave the process's memory).

## Sources

- [official] Caddy `handle_path` — https://caddyserver.com/docs/caddyfile/directives/handle_path
- [official] Caddy `reverse_proxy`, `flush_interval` — https://caddyserver.com/docs/caddyfile/directives/reverse_proxy#flush_interval
- [official] Caddy `try_files` — https://caddyserver.com/docs/caddyfile/directives/try_files
- [official] Playwright, managing browser binaries (`PLAYWRIGHT_BROWSERS_PATH`) — https://playwright.dev/python/docs/browsers#managing-browser-binaries
- [official] sslip.io — https://sslip.io
- [code] `code/deploy/Caddyfile`, `code/deploy/merchant-platform.service`, `code/Makefile` (`web-dist`, `deploy`), `code/sites/le-petit-zinc/booking.js`
- [issue] #142, #127, #132, #25
