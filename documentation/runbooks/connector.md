# Runbook: connect an AI assistant to the MCP gateway

Issues [#3](https://github.com/MaxEntz/BaselHack2026/issues/3), [#11](https://github.com/MaxEntz/BaselHack2026/issues/11) and [#14](https://github.com/MaxEntz/BaselHack2026/issues/14). Goal: book a table with our server's 6 tools from a conversation with any AI assistant.

Any MCP client works: Claude, Codex, OpenCode… What changes is where it calls from. Claude.ai and Claude Desktop call connectors **from Anthropic's cloud** (`160.79.104.0/21`), not from your browser, so `localhost` is not enough: they need a public HTTPS URL. Assistants running on your machine (Claude Code, Codex, OpenCode) can target `localhost` directly, or the public URL with a token.

**Connector URL:** `https://191-96-88-152.sslip.io/mcp`. It is served by our LXC (`191.96.88.152`), with no tunnel. For Claude.ai, go straight to step 5. For an assistant on your machine, see [Assistants on your machine](#assistants-on-your-machine).

⚠️ On this URL, only Anthropic's range (`160.79.104.0/21`) may call without a token. From anywhere else, `curl`, MCP Inspector or any assistant get `403 Forbidden` unless they send `Authorization: Bearer <GATEWAY_TOKEN>`. To test without it, target a local gateway, or run the SDK client on the server over SSH (see [Protect the public URL](#protect-the-public-url)).

## Validation status

| Link | Status | How |
|---|---|---|
| HTTPS on the public URL | ✅ validated | Spike #3: SDK client from outside, valid Let's Encrypt certificate |
| Same URL after an LXC reboot | ✅ validated | Spike #3: same URL after a reboot |
| `allowed_hosts`, `Origin`, bearer token, `https` redirect | ✅ validated locally | real server, headers simulated with curl |
| Claude Code (plan B), token | ✅ validated | Spike #3: real call, locally, with and without token |
| Claude Code (plan B), §9 scenarios | ✅ validated | the 6 tools, locally, see [User scenarios](#user-scenarios) |
| MCP client (e2e, in CI) | ✅ validated | `tests/e2e/test_gateway_mcp.py`, real server and real Postgres |
| Deployed server, 6 tools | ✅ validated | SDK client run on the LXC, on `http://127.0.0.1:8001/mcp`: booking, move, cancellation, party too large, outage and restore |
| Claude.ai, connection | ✅ validated | Spike #3: the former `ping` tool called from a conversation, screenshot below |
| Codex and OpenCode, booking | ✅ validated locally | real clients, gateway with a token, over MATP, see [User scenarios](#user-scenarios) |
| Public URL with a token | ⏳ to do | needs the deployment below; Caddy's rules checked on 2.6.2 in Docker, gateway's in the tests |
| Claude Desktop, §9 scenarios | 🔶 5 of 6 | by hand, through the connector, see [User scenarios](#user-scenarios) |
| Claude.ai's calls accepted by the server | ✅ validated | coming from `160.79.106.x`, all `200`, no `Host` or `Origin` rejected in the logs |
| Anthropic's calls through the filter | ✅ validated | Claude Desktop's tool calls came from `160.79.106.x`, all `200`, with the deployed Caddyfile |
| Caddy IP filter (#45): `403` outside `160.79.104.0/21` | ✅ validated | `403` and `server: Caddy` on `/mcp`, `/health` and `/.env` from outside, on the LXC |
| One restaurant's endpoint `/m/<slug>/mcp` (#23) | 🔶 locally | e2e, in-process and over MATP: 4 tools described from the profile, booking without `merchant`, `404`, `down`, new merchant without restart, older protocol. Claude.ai: ⏳ step 6 |
| MATP node on the LXC (#62) | ✅ validated | SDK client on the LXC, over MATP: booking, move, cancellation, party too large, outage and restore. `127.0.0.1:4710` only, closed from outside. Node stopped: the gateway stays up, gives the fallback link, and recovers without a restart |
| SSH tunnel (`ssh -L`) | ❌ refused | `AllowTcpForwarding no` in the server's SSH hardening |
| MCP Inspector | ✅ validated | `tools/list` locally: the 6 tools, their schemas and annotations |
| ngrok tunnel (steps 1 to 3) | ⏳ not tested | alternative if the LXC is no longer available |

![Claude.ai calls ping and shows pong](img/connector-ping.png)

*Spike #3: Claude.ai calls the former `ping` tool and shows `pong` with the server time (the conversation is in French). The tool has since been removed.*

## Deploy a change to the LXC

```bash
cd code
make deploy
```

You need the key `~/.ssh/baselhack2026_lxc`. If it isn't on your machine, ask Del, who may have it. A teammate can also have their public key added to the LXC, then run `make deploy DEPLOY_SSH="ssh -i <their-key> -o IdentitiesOnly=yes"`. A private key is never committed.

```bash
ssh -i ~/.ssh/baselhack2026_lxc -o IdentitiesOnly=yes root@191.96.88.152
```

`make deploy` mirrors your local `code/` (except `web/`, `.env`, `.venv` and caches) to `/opt/merchant-agents/code` with `rsync --delete`, so the server runs exactly your working tree, branch and uncommitted changes included. Nothing locks it: two people deploying at the same time overwrite each other, and the last one wins. Tell the team before deploying, and say which branch is live.

On the server, Caddy handles HTTPS and forwards to `127.0.0.1:8001`. The `merchant-gateway` service runs as an unprivileged user. Its configuration is in `/opt/merchant-agents/code/.env`, and its logs with `journalctl -u merchant-gateway`. `make deploy` doesn't touch that `.env`: on a new server, create it with `DATABASE_URL`, without which the service doesn't start, and `GATEWAY_PUBLIC_URL=https://191-96-88-152.sslip.io`, without which every public request gets `421`.

`make deploy` first checks that the server's `.env` has `GATEWAY_TOKEN` and `GATEWAY_TOKENLESS_NETWORKS` (Caddy forwards any request with a bearer token, so the gateway must have one to check it) and, with `MATP_URL`, the gateway's identity in `MATP_TOKENS`:

```bash
GATEWAY_TOKEN=…                                   # python3 -c "import secrets; print(secrets.token_urlsafe(32))"
GATEWAY_TOKENLESS_NETWORKS='["160.79.104.0/21"]'  # same list as the Caddyfile
```

To check that the gateway is running and reaches Postgres, run this on the server (`curl` isn't installed there):

```bash
cd /opt/merchant-agents/code
.venv/bin/python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8001/health').read().decode())"
```

⚠️ **The LXC is shared, it doesn't only run the gateway.** Ask before rebooting it, stopping a service or touching anything outside `/opt/merchant-agents`. When `code/deploy/Caddyfile` changed, `make deploy` validates it, overwrites `/etc/caddy/Caddyfile` and restarts Caddy: any other site must also be declared there. The firewall (`/etc/nftables.conf`) only opens ports 22, 80 and 443 inbound, and refuses new outbound connections to private networks.

## Postgres on the LXC

`postgresql-16` runs on the LXC, listening on localhost only. `make deploy` applies the migrations but doesn't touch the data. `make seed` and `make outage` act on your local database. For the server's:

```bash
ssh -i ~/.ssh/baselhack2026_lxc -o IdentitiesOnly=yes root@191.96.88.152
cd /opt/merchant-agents/code
.venv/bin/python scripts/demo_reset.py --all --with-bookings      # known state: the 3 restaurants and tonight's bookings
.venv/bin/python scripts/set_status.py le-petit-zinc down         # outage (then `live`)
```

On a new server, create the database in UTF-8. Without a locale, Postgres picks `SQL_ASCII`: psycopg then returns bytes, and everything fails with `cannot use a string pattern on a bytes-like object`.

```sql
CREATE DATABASE merchant_agents OWNER merchant ENCODING 'UTF8' LC_COLLATE 'C.UTF-8' LC_CTYPE 'C.UTF-8' TEMPLATE template0;
```

## MATP node on the LXC

The MATP node ([`PROTOCOL.md`](../PROTOCOL.md)) serves the merchant agents. It runs on the LXC as the systemd service `merchant-node` (`code/deploy/merchant-node.service`), as the same unprivileged user as the gateway. The unit pins `MATP_HOST=127.0.0.1` and `MATP_PORT=4710`, which override `.env`. The gateway, on the same host, is its only client. Port 4710 is **not** exposed: the firewall only opens 22, 80 and 443. Opening it needs TLS and a firewall rule on a shared server, so ask first, in a separate issue.

`make deploy` installs, enables and restarts `merchant-node` along with `merchant-gateway`. Both read `/opt/merchant-agents/code/.env`, which `make deploy` doesn't touch. On the server, it needs:

```bash
MATP_TOKENS='{"urn:matp:agent:mcp-gateway":"…"}'   # node: JSON, one token per known agent, 16 characters or more
MATP_URL=matp://127.0.0.1:4710    # gateway: speak MATP to the node; without it, the gateway calls the agents in-process
MATP_TOKEN=…                      # gateway: its own token, the one listed in MATP_TOKENS
```

Generate a token with `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`. After editing `.env`, restart both services.

On the server:

```bash
systemctl restart merchant-node                   # restart the node (then merchant-gateway if its settings changed)
systemctl status merchant-node                    # state, last log lines
journalctl -u merchant-node -f                    # live logs: one JSON line per request (agent id, intent, merchant, status, latency)
journalctl -u merchant-node --since "-10min"      # recent logs
ss -ltnp | grep 4710                              # must show 127.0.0.1:4710 only
```

From your machine, the port must stay closed (the connection times out):

```bash
timeout 5 bash -c 'echo > /dev/tcp/191.96.88.152/4710' && echo OPEN || echo closed
```

If the node is down, the gateway's tools answer `agent_unavailable` with each restaurant's fallback URL (#61), and `journalctl -u merchant-node` says why. The usual causes are a missing `DATABASE_URL` or `MATP_TOKENS`, or Postgres down (`systemctl status postgresql`).

## Prerequisites

- A Claude account that can add a custom connector: Free (only 1), Pro, Max, Ultra, Team or Enterprise. On Team, only an Owner or Primary Owner can add it. On Enterprise, also a custom role with *Manage access to Libraries*.
- Only for the ngrok alternative: `uv`, `ngrok` and an ngrok account (free).

## Step by step

Steps 1 to 3 are only needed without the LXC, to expose a local server through a tunnel. With the LXC:

- step 4: the public URL answers `403` outside Anthropic's range without a token. Send the token, test a local gateway, or run the SDK client on the server (see [Protect the public URL](#protect-the-public-url));
- step 5: replace `<name>.ngrok-free.app` with `191-96-88-152.sslip.io`.

### 1. Set up ngrok (once)

```bash
ngrok config add-authtoken <token>   # https://dashboard.ngrok.com/get-started/your-authtoken
```

Get your free static domain at https://dashboard.ngrok.com/domains. It keeps the same URL after a restart, so the connector doesn't have to be recreated.

### 2. Set up the project

```bash
cd code
uv sync
test -f .env || cp .env.example .env
```

In `.env`, without `/mcp` at the end:

```bash
GATEWAY_PUBLIC_URL=https://<name>.ngrok-free.app
GATEWAY_TOKEN=
```

### 3. Start the server and the tunnel

```bash
make gateway   # terminal 1
make tunnel    # terminal 2
```

Check from another network if possible (4G/5G hotspot):

```bash
curl -i https://<name>.ngrok-free.app/mcp
```

A `400` shows that the request does reach the server (`401` if `GATEWAY_TOKEN` is set). A `421` means `GATEWAY_PUBLIC_URL` is wrong or the server wasn't restarted.

### 4. Test with MCP Inspector

```bash
npx -y @modelcontextprotocol/inspector --cli https://<name>.ngrok-free.app/mcp --transport http --method tools/list
```

Without Node, the SDK client runs the same test:

```bash
uv run python -c '
import asyncio
from mcp import Client

async def main():
    async with Client("https://<name>.ngrok-free.app/mcp") as client:
        print(await client.list_tools())

asyncio.run(main())'
```

### 5. Add the connector in Claude.ai

**Pro, Max, Free, Ultra**

1. Go to **Customize > Connectors** (https://claude.ai/customize/connectors).
2. Click **+ Add**, then **Add custom connector**.
3. Enter the name `merchant-agents` and the URL `https://<name>.ngrok-free.app/mcp`. The `/mcp` path is required.
4. For **Authentication**, choose **No sign in**.
5. Click **Add**.

**Team, Enterprise**

1. An Owner goes to **Organization settings > Connectors** (https://claude.ai/admin-settings/connectors).
2. They click **Add**, hover over **Custom**, then choose **Web**.
3. Each member then clicks **Connect** in **Customize > Connectors**.

The dialog's layout varies between organizations.

**In a conversation**

1. Open **+ > Connectors** and enable `merchant-agents`.
2. Ask: "Table for 4 tonight at 8pm in Mulhouse".
3. Approve the calls if Claude asks. It must propose times, then ask for confirmation before booking.

### 6. One restaurant's own connector (optional)

Each live restaurant also has its own endpoint, `/m/<slug>/mcp` (#23, ADR-0012), with only its 4 booking tools, no `search_merchants`, and descriptions naming the restaurant, its hours and its largest group. The cancellation policy, free text from the restaurant's website, is never put in a description. The profile card shows it under **This restaurant only**.

1. Add a connector as in step 5, named after the restaurant, with the URL `https://191-96-88-152.sslip.io/m/le-petit-zinc/mcp`.
2. In a conversation, enable only that connector and ask: "Table for 2 tomorrow at 8pm".
3. Claude must call `get_availability` directly, without a search, and confirm before booking.

An unknown restaurant or one still being built answers `404`; a `down` one keeps its endpoint and gives its fallback link. A restaurant onboarded after the gateway started has its endpoint at once, without `make deploy`.

## Assistants on your machine

No tunnel needed. Once `make gateway` is running, with **Claude Code** (plan B for the demo):

```bash
claude mcp add --transport http merchant-agents http://localhost:8001/mcp
# with GATEWAY_TOKEN:
claude mcp add --transport http merchant-agents http://localhost:8001/mcp --header "Authorization: Bearer <token>"
```

Run `/mcp` in a session to check the connection, then play the scenarios below. For one restaurant's own endpoint: `claude mcp add --transport http le-petit-zinc http://localhost:8001/m/le-petit-zinc/mcp`.

With **Codex**, which reads the token from an environment variable:

```bash
codex mcp add merchant-agents --url http://localhost:8001/mcp
# with GATEWAY_TOKEN:
codex mcp add merchant-agents --url http://localhost:8001/mcp --bearer-token-env-var MERCHANT_AGENTS_TOKEN
```

With **OpenCode**, in `opencode.json`:

```json
{
  "mcp": {
    "merchant-agents": {
      "type": "remote",
      "url": "http://localhost:8001/mcp",
      "oauth": false,
      "headers": { "Authorization": "Bearer <token>" }
    }
  }
}
```

Any other MCP client works the same way: Streamable HTTP, that URL, and the header when there is a token.

To reach the merchants on the LXC instead of a local copy, use `https://191-96-88-152.sslip.io/mcp` with the server's `GATEWAY_TOKEN` (see [Protect the public URL](#protect-the-public-url)). A hosted assistant from another vendor, calling from its own cloud without a header, needs its range added in both places listed there.

Claude Desktop is **not** an independent plan B: its custom connector also goes through Anthropic's cloud, so if the tunnel goes down, it goes down too.

## User scenarios

Start from a known database (locally: `make db`, `make migrate`, `make seed`; on the LXC: `demo_reset.py`, see [Postgres on the LXC](#postgres-on-the-lxc)). In Claude, under **Settings > Account > Instructions for Claude**, set: *"I'm vegetarian. My budget is mid-range (max €€). My name is Alex Martin."*

| Message | Expected | Plan B (Claude Code) | Claude Desktop |
|---|---|---|---|
| "Table for 4 tonight at 8pm in Mulhouse" | `filters` passed without repeating them, times proposed, **confirmation requested**, booking, summary | ✅ | ✅ |
| "Move it to 9pm" | Confirmation, then `update_reservation` | ✅ | ✅ |
| "Cancel my booking" | Confirmation, then `cancel_reservation` | ✅ | ✅ |
| "Book a table at a vegan place with a terrace in Mulhouse" | No results, the assistant offers to drop a filter | ✅ (earlier wording) | ⏳ |
| "Table for 8 at Le Petit Zinc" | Fallback link given (`party_too_large`) | ✅ | ✅ |
| Le Petit Zinc down (`make outage`, or `set_status.py` on the LXC), then "Table for 2 at Le Petit Zinc" | Fallback link given (`agent_unavailable`); back to `live`, it works again without a restart | ✅ | ✅ |

Claude Desktop ran on 2026-10-31 against the LXC, before the bridge to MATP. A booking through the bridge then passed on the LXC from Anthropic's cloud, with the Messages API connector rather than the Claude.ai app.

Codex 0.165 and OpenCode 2.0.32 (with a free model that is neither Claude nor OpenAI's) ran the same day, against a local gateway with a token, over MATP. Both used the tools with no change to them: filters passed without being repeated, a summary and a question before each write, `via = assistant` in the database. Codex booked, moved a booking and got the fallback link for a table for 8. OpenCode booked and cancelled.

Word the request as a booking. Asked for "A vegan place with a terrace", Claude Desktop and OpenCode searched on their own, and Codex asked for a city, then did the same. "Book a table at a vegan place with a terrace in Mulhouse" sent Codex to the connector, with no results and an offer to drop a filter. The Messages API connector used the tools with either wording. Name the city in demo prompts anyway: the demo restaurants are in Mulhouse.

Plan B ran on 2026-10-31 with Claude Code, locally, mostly with Sonnet: `get_availability` took 11 to 28 ms server-side, and no guest name appeared in the INFO logs. It also validated two merchant-side scenarios: a cancellation by the restaurant, whose notice Claude relays, and a blocked slot, which Claude no longer offers. For Claude.ai, store a screenshot of each conversation in `assets/screenshots/`.

Watch the confirmation: Claude's behaviour varies between conversations. With the first descriptions, it once booked as soon as the user picked a restaurant. With the current ones, it waited for the yes in 9 conversations out of 9 (Sonnet and Opus). In Claude.ai, check that no booking goes out before your "yes".

Fallback links and the fixtures' websites point to `localhost` (`make sites`), so they only open on the machine serving those sites: the demo's. Assistants notice: in Claude Desktop, Claude warned that the link probably won't open and suggested calling the restaurant. For a demo where the audience sees the link, the sites need a public URL (#25).

To simulate a slow agent, `CHAOS_DELAY_MS=3000` (with `CHAOS_MERCHANTS=le-petit-zinc` to target one merchant) triggers the 2 s timeout, then the fallback link.

## Protect the public URL

### On the LXC: Caddy's filter

Claude.ai calls connectors from Anthropic's outbound range, `160.79.104.0/21` ([official docs](https://platform.claude.com/docs/en/api/ip-addresses)), and can't send a token without the beta below. In `code/deploy/Caddyfile`, the `191-96-88-152.sslip.io` site forwards to the gateway requests from that range (`remote_ip` matcher) and requests with a bearer token, which the gateway checks. It answers `403 Forbidden` to all others, before they reach uvicorn ([ADR-0010](../adr/0010-allow-only-anthropics-outbound-range-on-the-public-gateway.md), [ADR-0013](../adr/0013-open-the-user-side-to-any-ai-assistant.md)). The Claude.ai connector stays on **No sign in**.

The list of networks lives in two places, which must stay the same: the Caddyfile and `GATEWAY_TOKENLESS_NETWORKS` in the server's `.env`.

Check after `make deploy`:

```bash
curl -i https://191-96-88-152.sslip.io/mcp   # from your machine: 403 Forbidden, only a server: Caddy header
ssh -i ~/.ssh/baselhack2026_lxc -o IdentitiesOnly=yes root@191.96.88.152 'journalctl -u merchant-gateway --since "-5min"'   # no line for your request
```

Then, from a Claude.ai conversation, the connector must still list its tools (step 5). From your machine, with the server's token, `curl -i -H "Authorization: Bearer <token>" https://191-96-88-152.sslip.io/health` must answer `200`.

To test without the token, two options:

- **Local gateway**: `make gateway`, then any assistant on your machine, MCP Inspector or the SDK client on `http://localhost:8001/mcp`.
- **The LXC's gateway, from the server**: you need the server's key. The SDK client, in the deployment's venv, calls the gateway on `127.0.0.1`, without going through Caddy:

  ```bash
  ssh -i ~/.ssh/baselhack2026_lxc -o IdentitiesOnly=yes root@191.96.88.152 \
    'cd /opt/merchant-agents/code && .venv/bin/python -c "
  import asyncio
  from mcp import Client

  async def main():
      async with Client(\"http://127.0.0.1:8001/mcp\") as client:
          print([t.name for t in (await client.list_tools()).tools])

  asyncio.run(main())"'
  ```

  An SSH tunnel (`ssh -L 8001:127.0.0.1:8001 …`) would be handier for Inspector or an assistant, but `/etc/ssh/sshd_config.d/00-hardening.conf` sets `AllowTcpForwarding no` (`administratively prohibited`). The LXC is shared: don't change that setting without agreement.

⚠️ If Anthropic changes its range, the connector breaks with a `403`: recheck the docs above and update the Caddyfile. The filter doesn't protect an ngrok URL (plan B): for that, see below.

### Token (`GATEWAY_TOKEN`)

`GATEWAY_TOKEN` requires `Authorization: Bearer <token>` on every request except `/health`. Empty, the server is open to anyone who knows the URL. `GATEWAY_TOKENLESS_NETWORKS` (a JSON list) lets callers from those networks omit the header; a token they do send is still checked. Without `GATEWAY_TOKEN`, the gateway refuses to start with that list.

⚠️ On the Claude.ai side, header authentication is **in beta, limited to some organizations**. Without a **Request headers** section in the dialog, you don't have access: if `GATEWAY_TOKEN` is set, the connector can only connect from a network in `GATEWAY_TOKENLESS_NETWORKS`.

If the section is there:

1. Leave **Authentication** on **No sign in**.
2. In **Request headers**, choose `authorization`.
3. Enter the full value `Bearer <token>`. Claude adds no prefix.

Authentication can't be changed afterwards: delete the connector and create it again. Don't pass the token in the URL (`?token=`), the docs advise against it.

With ngrok and without access to the beta, two options:

- Stop the tunnel outside tests and the demo. This is the simple solution.
- Filter `160.79.104.0/21` with an ngrok traffic policy (`--traffic-policy-file`). Not tested. Inspector and `curl` from your machine would then be blocked.

## Possible errors

| Symptom | Cause | Fix |
|---|---|---|
| `421 Invalid Host header` | Tunnel domain missing from `allowed_hosts` | Fix `GATEWAY_PUBLIC_URL`, restart `make gateway` |
| `403 Forbidden` (only a `server: Caddy` header) | Request without a bearer token from outside Anthropic's range, refused by Caddy on the LXC | Expected from your machine: send the token, or use a local gateway, or the SDK client on the server. If Claude.ai gets it, recheck Anthropic's range and the Caddyfile |
| `403 Invalid Origin header` | `Origin` sent outside `ALLOWED_ORIGINS` | Add it in `gateway/server.py`. The refused name is in the server logs |
| `agent_unavailable` without a link, `/health` at `503` | Postgres unreachable: wrong `DATABASE_URL`, or database not migrated | Fix `.env`, run the migrations |
| `401 Unauthorized` | Token missing, wrong, or without the `Bearer ` prefix | Fix the header, or empty `GATEWAY_TOKEN` |
| `make tunnel`: `GATEWAY_PUBLIC_URL is empty` | `.env` missing or incomplete | Step 2 |
| `make gateway`: `database_url Field required` | `.env` missing | Step 2 |
| `307` to `/mcp` | URL entered with a trailing `/` | Use exactly `/mcp` |
| `RuntimeError: Task group is not initialized` | MCP app mounted in another app without lifespan | Enter `mcp.session_manager.run()` in the host app's lifespan. Not relevant today: the app isn't mounted |
| "Couldn't reach" or blocked connection | Hackathon Wi-Fi, or network blocking | Retest over a 4G/5G hotspot |

## Decisions

- **SDK v2 kept** (`mcp` 2.3.0), no fallback to `mcp<2`. Validated from Claude.ai and Claude Code.
- **Public LXC rather than a tunnel**: a fixed URL, and the demo depends neither on a Mac nor on the hackathon Wi-Fi. In return, a change must be deployed before hosted assistants see it. ngrok stays the documented alternative.
- **IP filter plus token on the LXC**: Claude.ai can't send `GATEWAY_TOKEN` without the beta, so its range passes without one. Every other assistant sends the token. Scans without one stop before uvicorn ([ADR-0010](../adr/0010-allow-only-anthropics-outbound-range-on-the-public-gateway.md), [ADR-0013](../adr/0013-open-the-user-side-to-any-ai-assistant.md)).
- **`sslip.io`**: a free public name to get a certificate, without buying a domain. It's a third-party service: a real domain would be safer for the demo.
- **Bound to `127.0.0.1`**: only Caddy reaches the server, which isn't exposed directly. Issue #11 plans `0.0.0.0`; only switch if a container requires it.
- **A single uvicorn worker**: sessions of clients on the older protocol (before 2026-07-28) live in one process's memory.

## References

- [Custom connectors (support)](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp)
- [Add a custom connector](https://claude.com/docs/connectors/custom/add-unlisted)
- [Connector authentication](https://claude.com/docs/connectors/building/authentication.md)
- [Connector troubleshooting](https://claude.com/docs/connectors/building/troubleshooting.md)
- [MCP SDK v2: Deploy & scale](https://py.sdk.modelcontextprotocol.io/run/deploy/)
