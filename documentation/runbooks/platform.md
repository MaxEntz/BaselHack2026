# Runbook: Sésame (web app and platform) on the team server

Issue #142, [ADR-0016](../adr/0016-serve-the-web-app-and-the-platform-from-the-team-server.md). The gateway and the BATP node are in [connector.md](connector.md).

⚠️ **Not deployed yet.** Merchant accounts (#132) are merged, and `make deploy` refuses a platform without their settings (step 2). Deploy it only after the team agreed to the server changes below.

⚠️ **The server is shared.** Ask before rebooting it, stopping a service or changing anything outside `/opt/merchant-agents`. When `code/deploy/Caddyfile` changes, `make deploy` validates it, overwrites `/etc/caddy/Caddyfile` and restarts Caddy: every other site must be declared in it.

## Addresses

| Host | What |
|---|---|
| `https://sesame.191-96-88-152.sslip.io` | The merchant web app; `/api/*` is the platform API |
| `https://sesame.191-96-88-152.sslip.io/manage/<slug>` | A merchant's management site (#127) |
| `https://le-petit-zinc.191-96-88-152.sslip.io` | Demo site Le Petit Zinc, with its booking form |
| `https://la-graine-verte.191-96-88-152.sslip.io`, `https://maison-albane.191-96-88-152.sslip.io` | The other demo sites |
| `https://191-96-88-152.sslip.io/mcp` | The gateway, unchanged: Anthropic's range only |

## First deployment (once)

1. **Chromium's system libraries** (outside `/opt`, needs the team's agreement), on the server:

   ```bash
   ssh -i ~/.ssh/baselhack2026_lxc -o IdentitiesOnly=yes root@191.96.88.152
   cd /opt/merchant-agents/code
   /opt/uv/bin/uv sync --frozen --no-dev --package builder -q
   .venv/bin/playwright install-deps chromium      # apt packages
   ```

2. **The platform's own settings**, in `/opt/merchant-agents/platform/.env` (never committed). The platform runs as its own user, `sesame`, which can't read `code/.env` (the gateway's and the node's settings, with `BATP_TOKENS` and `GATEWAY_TOKEN`). `make deploy` creates the user and sets the permissions (`root:sesame`, `640`):

   ```bash
   mkdir -p /opt/merchant-agents/platform
   cat > /opt/merchant-agents/platform/.env <<'ENV'
   DATABASE_URL=…                 # same database as in code/.env
   OPENAI_API_KEY=…
   GATEWAY_PUBLIC_URL=https://191-96-88-152.sslip.io
   PLATFORM_PUBLIC_URL=https://sesame.191-96-88-152.sslip.io
   PUBLIC_CORS_ORIGINS=["https://le-petit-zinc.191-96-88-152.sslip.io"]
   PLATFORM_DEV_ROUTES=0
   CRAWL_ALLOW_PRIVATE=0
   PLATFORM_WEB_URL=https://sesame.191-96-88-152.sslip.io
   PLATFORM_COOKIE_SECRET=…       # openssl rand -hex 32
   PLATFORM_INVITE_CODE=…         # at least 8 characters, given to the merchants we invite
   ENV
   ```

   `make deploy` refuses to go on without this file, `PLATFORM_DEV_ROUTES=0`, an `https://sesame.…` `PLATFORM_WEB_URL` (the start of the owners' secret links, and `Secure` cookies), a cookie secret (without it, every deploy logs every owner out) and an invite code (without it, anyone can start a build: an OpenAI call and a crawl from the shared server). It also refuses `PLATFORM_MERCHANT`, which is for local development. Don't add a `log` directive to the `sesame.` block of the Caddyfile without filtering `token`: the secret links go through it.

3. From your machine, with Node installed (`make deploy` builds `web/dist` first):

   ```bash
   cd code
   make deploy
   ```

   On top of the gateway and the node, it now ships `web/dist` (both copies readable by everyone, `--chmod=a+rX`, so `sesame` and Caddy can read them), creates the `sesame` user if needed, syncs the platform and the builder, installs Chromium under `/opt/merchant-agents/ms-playwright`, gives `sesame` a writable `.cache`, and installs and restarts `merchant-platform`.

## Operate it

On the server:

```bash
systemctl status merchant-platform
systemctl restart merchant-platform                # in-memory chat sessions are lost
journalctl -u merchant-platform -f                 # JSON lines: admin_tool, build_agent, builder_cache…
ss -ltnp | grep 8002                               # 127.0.0.1:8002 only
```

The platform's health, from the server (`curl` isn't installed there):

```bash
cd /opt/merchant-agents/code
.venv/bin/python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8002/health').read().decode())"
```

## Check after a deployment

- [ ] `systemctl status merchant-platform` is `active`: the `sesame` user can read the code. `make deploy` ships it with `rsync --chmod=a+rX`, so a checkout made with `umask 077` still gives readable files.
- [ ] From a phone on a mobile network, `https://sesame.191-96-88-152.sslip.io` opens the web app.
- [ ] Building a merchant from `https://le-petit-zinc.191-96-88-152.sslip.io` reads 5 pages (the crawl reaches the site with `CRAWL_ALLOW_PRIVATE=0`), then publishes it.
- [ ] A booking made from Claude appears live in the merchant's chat ("New booking via Claude").
- [ ] The booking form of `https://le-petit-zinc.191-96-88-152.sslip.io/reservation.html` books (CORS from that host).
- [ ] `curl -i https://191-96-88-152.sslip.io/mcp` from your machine still answers `403`.
- [ ] The server's other sites still answer.

## Possible errors

| Symptom | Cause | Fix |
|---|---|---|
| `make deploy`: `../platform/.env on the server needs …` | The platform's settings file is missing, keeps the dev routes on, or lacks one of #132's settings | Step 2 |
| `make deploy`: `../platform/.env on the server must not set PLATFORM_MERCHANT` | A development setting was copied to the server | Remove it from `platform/.env` |
| The `crawl` step fails with "Something went wrong while reading the website" | Chromium's libraries are missing | Step 1, then `journalctl -u merchant-platform` |
| The crawl says the address points to a private network, or that websites answer on ports 80 and 443 | `CRAWL_ALLOW_PRIVATE=0`: no private address, no port other than 80 and 443, so a build can't read the server's own services | Use the demo sites' public hosts, never `localhost` or a port |
| The booking form switches to "call us" mode | CORS: Le Petit Zinc's host missing from `PUBLIC_CORS_ORIGINS`, or the platform down | Step 2 (`PUBLIC_CORS_ORIGINS` in `platform/.env`); `systemctl status merchant-platform` |
| Live events stay on "Reconnecting" | `/api/events` buffered | Check `flush_interval -1` in the Caddyfile's `/api/*` block |
| A seeded merchant's fallback link opens `localhost:8080` | The fixtures still hold local addresses | Build the merchant from its public host instead, or re-seed with the server's addresses (#25) |
