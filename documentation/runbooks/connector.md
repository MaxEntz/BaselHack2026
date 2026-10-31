# Runbook: connect Claude to the MCP gateway

Spike [#3](https://github.com/MaxEntz/BaselHack2026/issues/3). Goal: call our server's `ping` tool from a Claude conversation.

Claude.ai calls connectors **from Anthropic's cloud** (`160.79.104.0/21`), not from your browser. So `localhost` is not enough: you need a public HTTPS URL. The same goes for Claude Desktop. Only Claude Code can target `localhost` directly.

**Connector URL:** `https://191-96-88-152.sslip.io/mcp`. It is served by our LXC (`191.96.88.152`), with no tunnel. Go straight to step 5.

⚠️ This URL only answers Anthropic's range (`160.79.104.0/21`): from your machine, `curl`, MCP Inspector or Claude Code get `403 Forbidden`. To test, target a local gateway, or run the SDK client on the server over SSH (see [Protect the public URL](#protect-the-public-url)).

## Validation status

| Link | Status | How |
|---|---|---|
| `ping` over HTTPS on the public URL | ✅ validated | SDK client from outside, valid Let's Encrypt certificate |
| Same URL after an LXC reboot | ✅ validated | same URL, `ping` OK after reboot |
| `allowed_hosts`, `Origin`, bearer token, `https` redirect | ✅ validated locally | real server, headers simulated with curl |
| Claude Code (plan B), with and without token | ✅ validated | real `ping` call, locally |
| Claude.ai | ✅ validated | `ping` called from a conversation, screenshot below |
| Claude.ai's calls accepted by the server | ✅ validated | coming from `160.79.106.x`, all `200`, no `Host` or `Origin` rejected in the logs |
| Caddy IP filter (#45): `403` outside `160.79.104.0/21` | ✅ validated locally | Caddy 2.6.2 (the LXC's version) in Docker: allowed range → gateway, otherwise `403`. On the LXC: to recheck at the first `make deploy` after merge |
| SDK client run on the LXC over SSH | ✅ validated | `tools/list` on `http://127.0.0.1:8001/mcp` |
| SSH tunnel (`ssh -L`) | ❌ refused | `AllowTcpForwarding no` in the server's SSH hardening |
| MCP Inspector | ⏳ not run yet | step 4 |
| ngrok tunnel (steps 1 to 3) | ⏳ not tested | alternative if the LXC is no longer available |

![Claude.ai calls ping and shows pong](img/connector-ping.png)

*Claude.ai calls the connector's `ping` tool and shows `pong` with the server time (the conversation is in French).*

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

⚠️ **The LXC is shared, it doesn't only run the gateway.** Ask before rebooting it, stopping a service or touching anything outside `/opt/merchant-agents`. When `code/deploy/Caddyfile` changed, `make deploy` validates it, overwrites `/etc/caddy/Caddyfile` and restarts Caddy: any other site must also be declared there. The firewall (`/etc/nftables.conf`) only opens ports 22, 80 and 443 inbound, and refuses new outbound connections to private networks.

## Prerequisites

- A Claude account that can add a custom connector: Free (only 1), Pro, Max, Ultra, Team or Enterprise. On Team, only an Owner or Primary Owner can add it. On Enterprise, also a custom role with *Manage access to Libraries*.
- Only for the ngrok alternative: `uv`, `ngrok` and an ngrok account (free).

## Step by step

Steps 1 to 3 are only needed without the LXC, to expose a local server through a tunnel. With the LXC:

- step 4: the public URL answers `403` outside Anthropic's range. Test a local gateway, or run the SDK client on the server (see [Protect the public URL](#protect-the-public-url));
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
npx -y @modelcontextprotocol/inspector --cli https://<name>.ngrok-free.app/mcp --transport http --method tools/call --tool-name ping
```

Without Node, the SDK client runs the same test:

```bash
uv run python -c '
import asyncio
from mcp import Client

async def main():
    async with Client("https://<name>.ngrok-free.app/mcp") as client:
        print(await client.call_tool("ping", {}))

asyncio.run(main())'
```

`ping` is the spike's health check. The #11 gateway, with the six user tools, may not keep it (the build deployed on the LXC on 2026-10-31 doesn't): then use `tools/list` (or `client.list_tools()`) instead.

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
2. Ask: "Call the ping tool of the merchant-agents connector". If the deployed gateway has no `ping` (see step 4), ask "Which tools does the merchant-agents connector have?" instead.
3. Approve the call if Claude asks. The answer must mention `pong` and the server time, or list the six user tools, from `search_merchants` to `get_my_reservations`.

## Plan B: Claude Code

No tunnel needed. Once `make gateway` is running:

```bash
claude mcp add --transport http merchant-agents http://localhost:8001/mcp
# with GATEWAY_TOKEN:
claude mcp add --transport http merchant-agents http://localhost:8001/mcp --header "Authorization: Bearer <token>"
```

Run `/mcp` in a session to check the connection, then ask it to call `ping`.

Claude Code can't target the LXC's gateway: the public URL answers it with `403`, and the server refuses SSH tunnels (see [Protect the public URL](#protect-the-public-url)).

Claude Desktop is **not** an independent plan B: its custom connector also goes through Anthropic's cloud, so if the tunnel goes down, it goes down too.

## Protect the public URL

### On the LXC: IP filter in Caddy

Claude.ai calls connectors from Anthropic's outbound range, `160.79.104.0/21` ([official docs](https://platform.claude.com/docs/en/api/ip-addresses)). In `code/deploy/Caddyfile`, the `191-96-88-152.sslip.io` site only forwards requests from that range to the gateway (`remote_ip` matcher) and answers `403 Forbidden` to all others, before they reach uvicorn ([ADR-0010](../adr/0010-allow-only-anthropics-outbound-range-on-the-public-gateway.md)). The connector stays on **No sign in**.

Check after `make deploy`:

```bash
curl -i https://191-96-88-152.sslip.io/mcp   # from your machine: 403 Forbidden, only a server: Caddy header
ssh -i ~/.ssh/baselhack2026_lxc -o IdentitiesOnly=yes root@191.96.88.152 'journalctl -u merchant-gateway --since "-5min"'   # no line for your request
```

Then, from a Claude.ai conversation, the connector must still answer (step 5).

To test without opening the URL, two options:

- **Local gateway**: `make gateway`, then Claude Code (plan B), MCP Inspector or the SDK client on `http://localhost:8001/mcp`.
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

  An SSH tunnel (`ssh -L 8001:127.0.0.1:8001 …`) would be handier for Inspector or Claude Code, but `/etc/ssh/sshd_config.d/00-hardening.conf` sets `AllowTcpForwarding no` (`administratively prohibited`). The LXC is shared: don't change that setting without agreement.

⚠️ If Anthropic changes its range, the connector breaks with a `403`: recheck the docs above and update the Caddyfile. The filter doesn't protect an ngrok URL (plan B): for that, see below.

### Token (`GATEWAY_TOKEN`)

`GATEWAY_TOKEN` requires `Authorization: Bearer <token>` on every request. Empty, the server is open to anyone who knows the URL.

⚠️ On the Claude.ai side, header authentication is **in beta, limited to some organizations**. Without a **Request headers** section in the dialog, you don't have access: if `GATEWAY_TOKEN` is set, the connector won't be able to connect.

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
| `403 Forbidden` (only a `server: Caddy` header) | Request from outside Anthropic's range, refused by Caddy on the LXC | Expected from your machine: local gateway, or SDK client on the server. If Claude.ai gets it, recheck Anthropic's range and the Caddyfile |
| `403 Invalid Origin header` | `Origin` sent outside `ALLOWED_ORIGINS` | Add it in `gateway/server.py`. The refused name is in the server logs |
| `401 Unauthorized` | Token missing, wrong, or without the `Bearer ` prefix | Fix the header, or empty `GATEWAY_TOKEN` |
| `make tunnel`: `GATEWAY_PUBLIC_URL is empty` | `.env` missing or incomplete | Step 2 |
| `make gateway`: `database_url Field required` | `.env` missing | Step 2 |
| `307` to `/mcp` | URL entered with a trailing `/` | Use exactly `/mcp` |
| `RuntimeError: Task group is not initialized` | MCP app mounted in another app without lifespan | Enter `mcp.session_manager.run()` in the host app's lifespan. Not relevant today: the app isn't mounted |
| "Couldn't reach" or blocked connection | Hackathon Wi-Fi, or network blocking | Retest over a 4G/5G hotspot |

## Decisions

- **SDK v2 kept** (`mcp` 2.3.0), no fallback to `mcp<2`. Validated from Claude.ai and Claude Code.
- **Public LXC rather than a tunnel**: a fixed URL, and the demo depends neither on a Mac nor on the hackathon Wi-Fi. In return, a change must be deployed before Claude.ai sees it. ngrok stays the documented alternative.
- **IP filter rather than token on the LXC**: Claude.ai can't send `GATEWAY_TOKEN` without the beta. Only Anthropic's range gets past Caddy; scans stop before uvicorn ([ADR-0010](../adr/0010-allow-only-anthropics-outbound-range-on-the-public-gateway.md)).
- **`sslip.io`**: a free public name to get a certificate, without buying a domain. It's a third-party service: a real domain would be safer for the demo.
- **Bound to `127.0.0.1`**: only Caddy reaches the server, which isn't exposed directly. Issue #11 plans `0.0.0.0`; only switch if a container requires it.
- **A single uvicorn worker**: sessions of clients on the older protocol (before 2026-07-28) live in one process's memory.

## References

- [Custom connectors (support)](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp)
- [Add a custom connector](https://claude.com/docs/connectors/custom/add-unlisted)
- [Connector authentication](https://claude.com/docs/connectors/building/authentication.md)
- [Connector troubleshooting](https://claude.com/docs/connectors/building/troubleshooting.md)
- [MCP SDK v2: Deploy & scale](https://py.sdk.modelcontextprotocol.io/run/deploy/)
