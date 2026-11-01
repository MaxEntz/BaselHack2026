# Runbook: connect Claude, or any AI assistant, to the MCP gateway

Goal: book a table with the gateway's 6 tools from a Claude.ai conversation, or from any MCP client (Codex, OpenCode, Claude Code…), through the team server.

Claude.ai calls connectors **from Anthropic's cloud** (`160.79.104.0/21`), not from your browser, so it needs a public HTTPS URL. Ours is served by the team server (LXC `191.96.88.152`) behind Caddy ([ADR-0009](../adr/0009-serve-the-gateway-from-the-team-server-behind-caddy.md)):

**Connector URL:** `https://191-96-88-152.sslip.io/mcp`

⚠️ The gateway always requires `GATEWAY_TOKEN` ([ADR-0019](../adr/0019-a-token-for-every-caller-added-by-caddy-for-claude-ai.md)). Claude.ai can't send it, so Caddy adds it to requests from Anthropic's range. From anywhere else, a request without `Authorization: Bearer <token>` gets `403 Forbidden` from Caddy, and one with a wrong token `401` from the gateway. With the token, any assistant on your machine can use the URL ([Other assistants](#other-assistants-codex-opencode-claude-code)).

## Validation status

Everything else in this runbook has been run end to end: deploy, HTTPS, Caddy's filter, the 6 tools and the BATP node on the server, Claude.ai and Claude Desktop through the connector, Claude Code locally. Status of the checks:

| Check | Status | Issue |
|---|---|---|
| One restaurant's endpoint `/m/<slug>/mcp` from Claude.ai | 🔶 validated locally (e2e, in-process and over BATP), ⏳ Claude.ai (step 3) | [#23](https://github.com/MaxEntz/BaselHack2026/issues/23) |
| Claude Desktop, §9 scenarios | ✅ on the deployed server, 2026-11-01: the six scenarios of step 4, each write after a "Yes"; ⏳ the pre-confirmed booking, not retested there | [#14](https://github.com/MaxEntz/BaselHack2026/issues/14) |
| ngrok tunnel | ⏳ never tested, plan B only | — |
| Public URL with a token (ADR-0019): Claude.ai through Caddy's added token, another assistant with its own | ✅ on the server (#142): `/mcp` answers `403` from outside Anthropic's range, `401` on loopback without the token, the 6 tools with it; Claude Desktop booked through it (#14) | #71 |

![Claude.ai calls ping and shows pong](img/connector-ping.png)

*Spike #3: Claude.ai calls the former `ping` tool through the connector (the conversation is in French). The tool has since been removed.*

## 1. Deploy

```bash
cd code
make deploy
```

You need the key `~/.ssh/baselhack2026_lxc`. If it isn't on your machine, ask the team. A teammate can also have their public key added to the server, then run `make deploy DEPLOY_SSH="ssh -i <their-key> -o IdentitiesOnly=yes"`. A private key is never committed.

What `make deploy` ships:

- **Checks:** refuses to go on, before copying anything, without the platform's own `/opt/merchant-agents/platform/.env`; after `uv sync`, before restarting anything, without the settings the platform must load from it (`scripts/platform_env.py`, [platform.md](platform.md), step 2) or the gateway's `GATEWAY_TOKEN`.
- **Code:** mirrors your local `code/` to `/opt/merchant-agents/code` with `rsync --delete` (except every `.env*` file, `.venv`, caches and test artifacts; of `web/`, only `web/dist`, built first by `make web-dist`, which needs Node). The server runs exactly your working tree, branch and uncommitted changes included. Nothing locks it: the last deploy wins, so tell the team which branch is live.
- **Python:** `uv sync` of the `gateway`, `merchant_node`, `merchant-platform` and `builder` packages, Chromium for the crawler, then `alembic upgrade head` on the server's Postgres. It never seeds.
- **Services:** installs, enables and restarts `merchant-node` (BATP, `127.0.0.1:4710`) and `merchant-gateway` (MCP, `127.0.0.1:8001`), both as an unprivileged user, and `merchant-platform` (`127.0.0.1:8002`, user `sesame`, see [platform.md](platform.md)). The gateway keeps a single uvicorn worker: sessions of clients on the older MCP protocol (before 2026-07-28) live in one process's memory.

What it touches **outside** `/opt/merchant-agents`:

- `/etc/systemd/system/merchant-node.service`, `merchant-gateway.service` and `merchant-platform.service`.
- The system user `sesame`, created if missing.
- `/etc/caddy/Caddyfile`, only when `code/deploy/Caddyfile` changed: it validates it, overwrites the file and restarts Caddy. **The server is shared**: any other site must also be declared in our Caddyfile.
- `/etc/systemd/system/caddy.service.d/merchant-agents.conf` (`code/deploy/caddy-merchant-agents.conf`), a drop-in that gives Caddy `GATEWAY_TOKEN` from `/opt/merchant-agents/caddy.env`. `make deploy` writes that file with the token the gateway reads from `code/.env` (`scripts/caddy_env.py`, `root:root 600`, read by systemd before Caddy drops its privileges) and restarts Caddy when it, the drop-in or the Caddyfile changed. All of this happens before the gateway restarts, so Claude.ai never meets a gateway that wants a token Caddy doesn't add yet.

It never touches `/opt/merchant-agents/code/.env`, and stops before touching Caddy or the services when the gateway would load no `GATEWAY_TOKEN` from it: Caddy forwards any request with a bearer token, so the gateway must check one. On a new server, create it with these lines (keep `GATEWAY_TOKEN`'s comment on its own line, as here):

```bash
DATABASE_URL=postgresql+psycopg://merchant:…@127.0.0.1:5432/merchant_agents   # without it, neither service starts
GATEWAY_PUBLIC_URL=https://191-96-88-152.sslip.io   # no /mcp; without it, every public request gets 421
BATP_TOKENS='{"urn:batp:agent:claude-gateway":"…"}' # node: one token per known agent, 16+ characters
# with `make guest` (#252), also its own: '{"urn:batp:agent:claude-gateway":"…","urn:batp:agent:sesame-guest":"…"}'
BATP_URL=batp://127.0.0.1:4710                      # gateway: go through the node
BATP_TOKEN=…                                        # gateway: its token, the one listed in BATP_TOKENS
# every caller; Caddy adds it for Claude.ai (ADR-0019)
GATEWAY_TOKEN=…
```

Generate a token with `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`, and restart both services after editing `.env`. On a new server, also create the database in UTF-8, or psycopg returns bytes and everything fails with `cannot use a string pattern on a bytes-like object`:

```sql
CREATE DATABASE merchant_agents OWNER merchant ENCODING 'UTF8' LC_COLLATE 'C.UTF-8' LC_CTYPE 'C.UTF-8' TEMPLATE template0;
```

⚠️ Ask the team before rebooting the server, stopping a service or changing anything outside `/opt/merchant-agents`. The firewall (`/etc/nftables.conf`) opens only 22, 80 and 443 inbound: port 4710 stays closed, and opening it needs TLS and a firewall rule (ask first).

## 2. Check it on the server

SSH port forwarding is disabled on the server (`AllowTcpForwarding no`; don't change it without agreement), so checks run there, in the deployment's venv (`curl` isn't installed):

```bash
ssh -i ~/.ssh/baselhack2026_lxc -o IdentitiesOnly=yes root@191.96.88.152
cd /opt/merchant-agents/code

# gateway up and reaching Postgres
.venv/bin/python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8001/health').read().decode())"

# the 6 tools, through the gateway, the node and Postgres, without Caddy (with the token)
.venv/bin/python -c "
import asyncio, httpx2
from core.config import get_settings
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

async def main():
    token = get_settings().gateway_token.get_secret_value()
    async with httpx2.AsyncClient(headers={'Authorization': f'Bearer {token}'}) as http:
        async with Client(streamable_http_client('http://127.0.0.1:8001/mcp', http_client=http)) as client:
            print([t.name for t in (await client.list_tools()).tools])

asyncio.run(main())"

# the node
systemctl status merchant-node merchant-gateway
journalctl -u merchant-node -f          # one JSON line per request: agent, intent, merchant, status, latency
ss -ltnp | grep 4710                    # must show 127.0.0.1:4710 only
```

Start the scenarios from a known state, still on the server:

```bash
.venv/bin/python scripts/demo_reset.py --all --with-bookings   # the 3 restaurants and tonight's bookings
.venv/bin/python scripts/demo_reset.py --all --with-bookings --with-management   # + La Graine Verte's management data (#131)
.venv/bin/python scripts/set_status.py le-petit-zinc down      # outage, then `live` to restore
.venv/bin/python scripts/demo_booking.py                       # the live booking, through the local gateway
```

(`make seed`, `make demo-reset` and `make outage` act on your **local** database; `make demo-booking` on your local gateway.) Seeded merchants have no owner link: issue one from `/opt/merchant-agents/platform`, not from here ([platform.md](platform.md), step 5).

From your machine, the public URL must refuse you without the token, let you through with it, and port 4710 must stay closed:

```bash
curl -i https://191-96-88-152.sslip.io/mcp     # 403 Forbidden, only a `server: Caddy` header
curl -i -X POST -H "Authorization: Bearer wrong" https://191-96-88-152.sslip.io/mcp   # 401, from the gateway
curl -i -H "Authorization: Bearer <token>" https://191-96-88-152.sslip.io/health      # 200
timeout 5 bash -c 'echo > /dev/tcp/191.96.88.152/4710' && echo OPEN || echo closed
```

## 3. Add the connector in Claude.ai

You need a Claude account that can add a custom connector: Free (only 1), Pro, Max, Ultra, Team or Enterprise (on Team, an Owner or Primary Owner; on Enterprise, also a custom role with *Manage access to Libraries*).

**Pro, Max, Free, Ultra:** **Customize > Connectors** (https://claude.ai/customize/connectors) → **+ Add** → **Add custom connector**. Name `merchant-agents`, URL `https://191-96-88-152.sslip.io/mcp` (the `/mcp` path is required), **Authentication: No sign in** → **Add**.

**Team, Enterprise:** an Owner goes to **Organization settings > Connectors** (https://claude.ai/admin-settings/connectors) → **Add** → **Custom** → **Web**. Each member then clicks **Connect** in **Customize > Connectors**. The dialog's layout varies between organizations.

**In a conversation:** **+ > Connectors**, enable `merchant-agents`, ask "Table for 4 tonight at 8pm in Mulhouse". Claude must propose times, then ask for confirmation before booking.

**One restaurant's own connector** ([ADR-0012](../adr/0012-one-mcp-server-for-every-merchant-endpoint.md)): each live restaurant also has `/m/<slug>/mcp`, with its 4 booking tools (no `search_merchants`) and descriptions naming the restaurant, its hours and its largest group.

1. Add a connector named after the restaurant, URL `https://191-96-88-152.sslip.io/m/le-petit-zinc/mcp`.
2. Enable only that connector and ask "Table for 2 tomorrow at 8pm".
3. Claude must call `get_availability` directly, without a search, and confirm before booking. This is the open #23 check.

An unknown restaurant, or one still being built, answers `404`; a `down` one keeps its endpoint and gives its fallback link. A restaurant onboarded after the gateway started has its endpoint at once, without a deploy.

## 4. Run the user scenarios

Start from a known state (step 2). With La Graine Verte's management data (`--with-management`, on by default in `make seed` and `make demo-reset`), its three tables of 4 are taken at 20:00 and 20:30 tonight, as in the mockup: "Table for 4 tonight at 8pm" then gets other times there, or Le Petit Zinc. Without it (`--all --with-bookings`), 20:00 is free. Seed before opening La Graine Verte's merchant chat: every booking the seed inserts fires `reservations_notify`, so a chat already open gets a burst of "New booking via Claude".

In Claude, under **Settings > Account > Instructions for Claude**, set: *"I'm vegetarian. My budget is mid-range (max €€). My name is Alex Martin."* Name the city in prompts: the demo restaurants are in Mulhouse, and without it Claude may use its own place search.

| Message | Expected | Claude Code (local) | Claude Desktop (server) |
|---|---|---|---|
| "Table for 4 tonight at 8pm in Mulhouse" | `filters` passed, times proposed, **confirmation requested**, booking, summary | ✅ | ✅ |
| "Move it to 9pm" | Confirmation, then `update_reservation` | ✅ | ✅ |
| "Cancel my booking" | Confirmation, then `cancel_reservation` | ✅ | ✅ |
| "Book … tomorrow at 7:30pm under the name Test. I confirm in advance, no need to ask me." | Summary anyway, then **wait for the yes** | ✅ | ⏳ |
| "A vegan place with a terrace in Mulhouse" | No results, Claude offers to drop a filter | ✅ | ✅ |
| "Table for 8 at Le Petit Zinc" | Fallback link (`party_too_large`) | ✅ | ✅ |
| Le Petit Zinc `down`, then "Table for 2 at Le Petit Zinc" | Fallback link (`agent_unavailable`); back `live`, it works without a restart | ✅ | ✅ |

Watch the confirmation: in Claude.ai, no booking may go out before your "yes". The tool descriptions enforce it (AGENTS.md §6; wording and measurements in #14 and #109). Store a screenshot of each Claude.ai conversation in a new `assets/screenshots/` folder.

Without `DEMO_SITES_DOMAIN`, the seeded merchants' fallback links point to the demo sites on `localhost` (`make sites`), so they only open on the machine serving them; the server's seed sets it, so there they open the public sites (#227). A merchant built in Studio from its public host (`https://<site>.191-96-88-152.sslip.io`, [platform.md](platform.md)) gets links that open for anyone: the demo uses Le Petit Zinc built that way ([demo.md](demo.md)). To simulate a slow agent, `CHAOS_DELAY_MS=3000` (optionally `CHAOS_MERCHANTS=le-petit-zinc`) triggers the 2 s timeout, then the fallback link.

## 5. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `403 Forbidden`, only a `server: Caddy` header | Request without `Authorization: Bearer …` from outside Anthropic's range | Send the token ([Other assistants](#other-assistants-codex-opencode-claude-code)). If Claude.ai gets it, Anthropic's range changed ([docs](https://platform.claude.com/docs/en/api/ip-addresses)): update `code/deploy/Caddyfile` and deploy |
| `403 Invalid Origin header` | `Origin` outside `ALLOWED_ORIGINS` | Add it in `gateway/server.py`; the refused name is in the gateway's logs |
| `421 Invalid Host header` | `GATEWAY_PUBLIC_URL` missing or wrong in `.env` | Fix it (no `/mcp`), `systemctl restart merchant-gateway` |
| Every tool answers `agent_unavailable` with the fallback link | BATP node down or refusing the gateway | `journalctl -u merchant-node`: usually `DATABASE_URL` or `BATP_TOKENS` missing, or `BATP_TOKEN` not in `BATP_TOKENS`. The gateway recovers without a restart once the node is back |
| `agent_unavailable` without a link, `/health` at `503` | Postgres down or unreachable, or not migrated | `systemctl status postgresql`, check `DATABASE_URL`, rerun `make deploy` (it migrates) |
| A service doesn't start | `.env` missing or incomplete | `journalctl -u merchant-gateway` / `-u merchant-node`, then step 1's `.env` |
| `401 Unauthorized` | Wrong token, or header missing or without `Bearer ` (on a local gateway with `GATEWAY_TOKEN`) | Fix the header (see [Token](#token-gateway_token)) |
| Claude.ai gets `401` | Caddy has no `GATEWAY_TOKEN`, or an old one: `caddy.env` or the drop-in missing, or Caddy not restarted | Rerun `make deploy`; on the server, `systemctl show caddy -p EnvironmentFiles` must list `/opt/merchant-agents/caddy.env` |
| `307` to `/mcp` | URL entered with a trailing `/` | Use exactly `/mcp` |
| "Couldn't reach" in Claude.ai | Network or Caddy | Check step 2 on the server; retest over a 4G/5G hotspot |

## Other assistants (Codex, OpenCode, Claude Code)

Any MCP client over Streamable HTTP works, with the server's `GATEWAY_TOKEN` (ask the team; it is in `/opt/merchant-agents/code/.env`, never in the repo). The URL is the same, `https://191-96-88-152.sslip.io/mcp`, or `/m/<slug>/mcp` for one restaurant.

```bash
# Claude Code
claude mcp add --transport http merchant-agents https://191-96-88-152.sslip.io/mcp --header "Authorization: Bearer <token>"
# Codex, which reads the token from an environment variable
export MERCHANT_AGENTS_TOKEN=<token>
codex mcp add merchant-agents --url https://191-96-88-152.sslip.io/mcp --bearer-token-env-var MERCHANT_AGENTS_TOKEN
```

OpenCode, in `opencode.json`:

```json
{
  "mcp": {
    "merchant-agents": {
      "type": "remote",
      "url": "https://191-96-88-152.sslip.io/mcp",
      "oauth": false,
      "headers": { "Authorization": "Bearer <token>" }
    }
  }
}
```

Codex 0.165 and OpenCode 2.0.32 booked, moved and cancelled this way against a local gateway with a token (#71). Word the request as a booking ("Book a table at a vegan place with a terrace in Mulhouse"): asked for "a vegan place", some assistants search the web instead of calling the tools. Bookings they make are stored with `via = "claude"` for now, like any booking through the gateway.

A hosted assistant from another vendor that calls from its own cloud without a header would need its range added to the Caddyfile, like Anthropic's, or OAuth.

## Plan B: without the team server

### Claude Code on a local gateway (tested)

No public URL needed. Once `make gateway` is running:

```bash
claude mcp add --transport http merchant-agents http://localhost:8001/mcp
claude mcp add --transport http le-petit-zinc http://localhost:8001/m/le-petit-zinc/mcp   # one restaurant
```

Add `--header "Authorization: Bearer <token>"` when `GATEWAY_TOKEN` is set. Run `/mcp` in a session to check, then the scenarios of step 4. `npx @modelcontextprotocol/inspector` works the same way on `http://localhost:8001/mcp`. Claude Desktop is no plan B: its connectors also go through Anthropic's cloud.

### ngrok (⚠️ untested)

Only if the team server is unavailable. Needs `ngrok` and a free account with a static domain (https://dashboard.ngrok.com/domains), so the connector URL survives a restart.

```bash
ngrok config add-authtoken <token>
# code/.env: GATEWAY_PUBLIC_URL=https://<name>.ngrok-free.app   (no /mcp)
make gateway    # terminal 1
make tunnel     # terminal 2
curl -i https://<name>.ngrok-free.app/mcp   # 400 = reaches the gateway (401 with GATEWAY_TOKEN), 421 = wrong GATEWAY_PUBLIC_URL
```

Then step 3 with `https://<name>.ngrok-free.app/mcp`. Caddy's filter doesn't protect this URL: stop the tunnel outside tests, or set a token.

## Token (`GATEWAY_TOKEN`)

When set, `Authorization: Bearer <token>` is required on every request except `/health`. On the team server it is always set (`make deploy` refuses otherwise), and Caddy adds it for Anthropic's range, replacing any `Authorization` the request carried. The gateway never trusts a client address: a process on the shared server that forges `X-Forwarded-For` still needs the token. To rotate it, edit `code/.env` on the server and rerun `make deploy` (it restarts the gateway and, since `caddy.env` changed, Caddy); give the new value to whoever uses another assistant. On Claude.ai, header authentication is a **beta limited to some organizations**: without a **Request headers** section in the connector dialog, a connector can't send it. If the section is there, leave **Authentication** on **No sign in**, choose `authorization` and enter `Bearer <token>` in full. Authentication can't be changed afterwards (delete and recreate the connector). Never put the token in the URL.

## References

- [Custom connectors (support)](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp)
- [Add a custom connector](https://claude.com/docs/connectors/custom/add-unlisted)
- [Connector authentication](https://claude.com/docs/connectors/building/authentication.md)
- [Connector troubleshooting](https://claude.com/docs/connectors/building/troubleshooting.md)
- [MCP SDK v2: Deploy & scale](https://py.sdk.modelcontextprotocol.io/run/deploy/)
- Decisions: [ADR-0009](../adr/0009-serve-the-gateway-from-the-team-server-behind-caddy.md) (team server, `sslip.io`, loopback binding), [ADR-0010](../adr/0010-allow-only-anthropics-outbound-range-on-the-public-gateway.md) (IP filter), [ADR-0019](../adr/0019-a-token-for-every-caller-added-by-caddy-for-claude-ai.md) (a token for every caller), [ADR-0012](../adr/0012-one-mcp-server-for-every-merchant-endpoint.md) (per-restaurant endpoints)
