<p align="center"><img src="assets/logo/sesame-wordmark.png" alt="Sésame" width="380"></p>

# Sésame

> Let Claude book with any merchant, straight from their website.

A restaurant owner gives us their website and describes how they work. We generate their booking agent. Anyone can then book a table by asking Claude, through a single MCP server that applies their preferences and sends them back to the restaurant's own site whenever the agent can't help. The owner runs it by chatting with our platform, onboarding included, and sees bookings made through Claude appear live. Next to the chat, a management site (pages and forms, in the restaurant's own look) offers the same operations ([#127](https://github.com/MaxEntz/BaselHack2026/issues/127)). It all runs on our team server: [sesame.191-96-88-152.sslip.io](https://sesame.191-96-88-152.sslip.io) (building a new merchant there needs an invitation).

Built at BaselHack 2026. Issue [#26](https://github.com/MaxEntz/BaselHack2026/issues/26) tracks the plan.

![Studio after a build: the chat with what went into the profile, beside the agent site and the management site it built](documentation/img/sesame-studio.png)

![The management site's Home: tonight's bookings, the guests of the last 30 days, the room and the assistant](documentation/img/sesame-management-home.png)

*Studio, where a merchant builds and publishes its sites by chatting, then the management site those sites come with. Shown on the demo data (`VITE_MOCK=1`): the restaurants and the guests are fictional. Claude.ai calling our MCP gateway is in [this screenshot](documentation/runbooks/img/connector-ping.png).*

## How it works

- **User side.** Claude.ai calls our **gateway**, a remote MCP server, which routes the call to the merchant's **agent**. Each merchant's data lives in its own Postgres schema, and the database itself makes double booking impossible. On failure, the gateway returns the merchant's own booking page.
- **Merchant side.** The merchant uses **Studio**, a chat backed by the **platform API**, and a **management site** of ordinary pages over the same operations. There, an **assistant** answers through admin tools ("What bookings do I have tonight?", "Block 21:00"), and the **builder** turns the merchant's website into a validated profile, then provisions the agent. The LLM only fills that profile; it never writes code or SQL.
- **Agent network.** Behind the gateway, the merchant agents form a network reserved for AI agents, with its own protocol: **BATP**, on TCP port 4710. Every session names the calling agent, and every request names its intent and merchant. Claude only speaks MCP, so the gateway is its bridge to BATP. The spec is an RFC-style Internet-Draft, [`draft-merchant-agents-batp-00`](documentation/rfc/draft-merchant-agents-batp-00.txt), summarized in [`documentation/PROTOCOL.md`](documentation/PROTOCOL.md). The BATP node runs on the team server, and the deployed gateway reaches the agents through it.
- **Live.** A committed booking fires Postgres `NOTIFY`, which is pushed to the merchant's chat and management site over SSE, within 2 seconds (a test checks it).

➡️ **[documentation/ARCHITECTURE.md](documentation/ARCHITECTURE.md)** has the diagrams, the main flow step by step, every component and what it does *not* do, and the stack with its [decision records](documentation/adr/README.md).

## Quick start

Prerequisites: Python 3.12 with [uv](https://docs.astral.sh/uv/), Docker, Node.js (minimum version in [`code/README.md`](code/README.md#prerequisites)).

```bash
cd code
make setup && make db && make migrate   # dependencies, Postgres 16, migrations
make test                                # unit + integration tests (needs Docker)
VITE_MOCK=1 make web                     # Studio and the management site on mock data -> http://localhost:5173
```

To run it for real on your machine, set `OPENAI_API_KEY` in `code/.env` (copied from `.env.example`, with `CRAWL_ALLOW_PRIVATE=1`), then:

```bash
make demo-reset   # the demo merchants and their data
make gateway      # terminal 1: MCP server -> http://localhost:8001/mcp
make platform     # terminal 2: platform API -> http://localhost:8002
make web          # terminal 3: Studio -> http://localhost:5173, then build from http://localhost:8080
make sites        # terminal 4: the 3 demo websites
```

All the commands are in [`code/README.md`](code/README.md). To connect Claude.ai to the gateway, see [`documentation/runbooks/connector.md`](documentation/runbooks/connector.md); the 5-minute demo is [`documentation/runbooks/demo.md`](documentation/runbooks/demo.md).

## Repository layout

| Path | Content |
|---|---|
| [`code/`](code) | uv workspace (`core`, `agent`, `gateway`, `batp`, `merchant_node`, `builder`, `merchant_platform`), web chat, demo sites, migrations, scripts, tests |
| [`documentation/`](documentation) | Architecture, decision records, contracts, runbooks |
| [`assets/`](assets) | Presentation material |
| [`AGENTS.md`](AGENTS.md) | Full spec, shared by Claude Code (`CLAUDE.md`) and opencode |

## Team

| Name | GitHub |
|---|---|
| Enzo Laugel | [@Boz0o0](https://github.com/Boz0o0) |
| Maxime Entz | [@MaxEntz](https://github.com/MaxEntz) |
| Matteo Tutti | [@matteoepitech](https://github.com/matteoepitech) |
| Matthias Yildirim | [@Rimapus](https://github.com/Rimapus) |
| Nicolas Bach | [@TheNotSoChosen0ne](https://github.com/TheNotSoChosen0ne) |
| Valentino Zarrillo | [@Chrystoval1](https://github.com/Chrystoval1) |
| Yann Toison-Chabane | [@Yann-TC](https://github.com/Yann-TC) |

MIT licensed, see [`license.txt`](license.txt).
