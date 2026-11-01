# Runbook: the 5-minute demo

Issue [#25](https://github.com/MaxEntz/BaselHack2026/issues/25). This runbook covers the script, the checklist before going on stage, plan B and the jury's likely questions. How each piece is deployed and checked: [platform.md](platform.md) (Studio, the management site, the demo sites) and [connector.md](connector.md) (the gateway, the node, Claude.ai). Definition of done: AGENTS.md §11. The scenarios: AGENTS.md §9.

Since #254 the guest's Claude books by talking to the merchant's AI agent (`talk_to_merchant`, BATP `TALK`): the agent answers from the site, proposes, and its code books only after the guest's yes. Its answers are a model's, so the wording changes from one run to the next: rehearse on the real agent (#255).

## Addresses

| What | Address |
|---|---|
| Le Petit Zinc's own site, the one Studio reads | `https://le-petit-zinc.191-96-88-152.sslip.io` (booking form: `/reservation.html`) |
| Studio, which builds and publishes a merchant | `https://sesame.191-96-88-152.sslip.io` |
| A merchant's management site | `https://sesame.191-96-88-152.sslip.io/manage/<slug>`, opened once with the merchant's private link |
| The Claude.ai connector | `https://191-96-88-152.sslip.io/mcp`, named `merchant-agents` ([connector.md](connector.md), step 3) |

On the server, every command runs from `/opt/merchant-agents/code` with `.venv/bin/python`, except owner links, which run from `/opt/merchant-agents/platform` ([platform.md](platform.md), step 5). Connect over `ssh -i ~/.ssh/baselhack2026_lxc -o IdentitiesOnly=yes root@191.96.88.152`. `make demo-reset`, `make outage` and `make demo-booking` act on **your local** database and gateway, not the server's ([connector.md](connector.md), step 2).

## Roles (proposal, to agree on)

| Role | Screen | Does |
|---|---|---|
| Speaker | the slides ([Slides](#slides)) | Tells the story and keeps the time. Answers the jury, with the [answers below](#the-jurys-likely-questions) |
| Merchant | laptop A: Le Petit Zinc's site, Studio, the management site | Steps 1, 2, 4, 5, 7, 8 |
| Guest | laptop B: Claude.ai, its preferences set | Steps 3, 5, 6, 7 |
| Backstage | an SSH terminal on the server, plan B ready | Resets between rehearsals, watches `journalctl -u merchant-platform -u merchant-gateway -u merchant-node -f`, starts plan B or the video |

## Slides

The team's deck: [`sesame-pitch-v6.html`](../design/poc-v10/sesame-pitch-v6.html), with its recording [`sesame-demo.mp4`](../design/poc-v10/sesame-demo.mp4) beside it; how to run it is in [PITCH.md](../design/poc-v10/PITCH.md). Arrows move, F goes full screen, N shows the speaker notes. Slide 6, *Live demo*, plays the recording on arrival: a click or space pauses it, so leave the slide with the right arrow. That recording shows the v10 mockups, not the deployed app: it is not plan B's backup video.

## Before the demo

The day before:

- [ ] The commit you deploy passes the [rehearsal tests](#tests-before-each-rehearsal).
- [ ] `make deploy` done, then the whole checklist of [platform.md](platform.md#check-after-a-deployment) and [connector.md](connector.md#2-check-it-on-the-server), step 2.
- [ ] Builder cache warm ([ADR-0007](../adr/0007-builder-cache-for-a-replayable-demo.md)): one rehearsal build of Le Petit Zinc on the server, with `BUILDER_CACHE=read_write`, the default, using [the onboarding message](#the-rehearsed-message) word for word. Then set `BUILDER_CACHE=read_only` in `/opt/merchant-agents/platform/.env` and `systemctl restart merchant-platform`. The extraction's key holds what the crawl read, the description, `OPENAI_MODEL`, the prompt's version and the crawl's (`builder/cache.py`). So warm it up on the commit and the `OPENAI_MODEL` you demo with: a different word, model or deploy that changes a version means a live build. `build_agent`'s description is written by the assistant from the message, so it can differ between two runs. After the second rehearsal, check in `journalctl -u merchant-platform` that its `builder_cache` lines report a hit.
- [ ] Plan B tested on the demo laptop: Claude Code on a local gateway, with a local node that has an `OPENAI_API_KEY` (#250).
- [ ] Backup video of the full script recorded, in `assets/` and on the demo laptop.
- [ ] The deck and its recording on the speaker's laptop, opened in Chrome ([Slides](#slides)).

In this order, 30 minutes before:

1. [ ] **Known state**, on the server: `.venv/bin/python scripts/demo_reset.py`. **The server is shared: the reset erases every merchant, including a teammate's rehearsal. Tell the team first** ([platform.md](platform.md)). It applies the migrations, drops every merchant and seeds La Graine Verte and Maison Albane, live, with La Graine Verte's management data. Le Petit Zinc is left out, because Studio builds it live. The reset also empties the owners' links: issue a new one for any merchant whose management site you show ([platform.md](platform.md), step 5).
2. [ ] **Services**: `systemctl status merchant-node merchant-gateway merchant-platform` shows all `active`, and the platform's and the gateway's `/health` answer `ok` ([platform.md](platform.md#operate-it), [connector.md](connector.md#2-check-it-on-the-server)). From the laptop, `curl -i https://191-96-88-152.sslip.io/mcp` answers `403`: the filter is on.
   - [ ] **The merchant's agent has its key**: `journalctl -u merchant-node -b | grep -c "no OPENAI_API_KEY"` gives `0`. Otherwise every talk answers with the booking page: add the key to `/opt/merchant-agents/code/.env` and `systemctl restart merchant-node` (#250).
   - [ ] **One talk end to end**: in Studio's Agent sheet, ask "Do you have a table for 2 tonight?" and check the agent answers with times. Note how long it took: a turn takes a few seconds, and the node gives it 25 s.
3. [ ] **Studio invited**: open `https://sesame.191-96-88-152.sslip.io/?invite=<PLATFORM_INVITE_CODE>`. Once accepted, the code leaves the address bar. Without it, a build answers "Building a new agent needs an invitation: open the link you were given."
4. [ ] **Claude.ai**: the `merchant-agents` connector is connected and enabled in a new conversation (**+ > Connectors**). Under **Settings > Account > Instructions for Claude**, enter: *"I'm vegetarian. My budget is mid-range (max €€). My name is Alex Martin."* ([connector.md](connector.md), step 4).
5. [ ] **OpenAI credits**: the build, the merchant's assistant and now the merchant's AI agent, at every guest message, call the OpenAI API ([ADR-0004](../adr/0004-openai-for-backend-llm-calls.md)).
6. [ ] **The clock**: "tonight at 8pm" needs a demo before 20:00 (Europe/Zurich), and blocking 21:00 needs 21:00 still ahead. 21:00 is Le Petit Zinc's last seating: service ends at 22:00, minus 60 minutes. Later in the evening, say "tomorrow" everywhere.
7. [ ] **Network**: 4G/5G tethering ready. Claude.ai reaches the gateway from Anthropic's cloud, not from the hall's Wi-Fi.
8. [ ] **Screens**: OS notifications muted, browser zoom at 125%. Laptop A has three tabs: the site, then Studio, then a free tab for the private link. Laptop B has the Claude.ai conversation.

## The script

| # | Time | Who | What to do | What the room sees |
|---|---|---|---|---|
| 1 | 0:00 | Merchant | Open Le Petit Zinc's site | A fictional bistro in Mulhouse (every page says so): menu, hours, its own booking form |
| 2 | 0:30 | Merchant | In Studio, send [the onboarding message](#the-rehearsed-message), word for word | The steps run live while the page read appears beside the chat. Then "What went into your profile", with each fact's source and "Check these". The status reads "Hidden from guests", and "Your private link: keep it, it is shown only once." |
| | | Merchant | Send the [one addition](#the-rehearsed-message), then **Publish** | A card shows the change ("Cancelling"). Then "You're live. Agents get real answers now.", the status "Live", and "Your addresses" |
| | | Merchant | Open the private link in the free tab and leave it on Home: the management site must be open **before** the booking, since only a page already open gets the live event | The management site's Home |
| 3 | 2:00 | Guest | "Table for 4 tonight at 8pm in Mulhouse, do you have vegetarian dishes?". If Claude offers La Graine Verte first: "Le Petit Zinc, please". Then "Yes" once it asks | Claude searches with the preferences: Maison Albane (€€€€) is filtered out. Then it **talks to Le Petit Zinc's agent**, which answers from the site and offers times. The agent proposes the booking; Claude shows its summary and **waits for the yes**. Only then does it send the confirm, and the agent's code books |
| 4 | 3:00 | Merchant | Watch the management site's tab, then Studio's chat | The booking arrives with no reload. On the management site: a toast "New booking through your agent", the bell counts it, and Home's "Recent activity" lists it. The toast only shows while the assistant's panel and page are closed: at 125% zoom on a 1440 px screen the panel starts closed; when it is open, the booking shows in the panel instead. Studio's chat shows "New booking via Claude" too |
| 5 | 3:30 | Merchant | **Reservations** → **Block a time** → 21:00 → **Save**. Or ask the assistant's panel: "Block 21:00 tonight" | A toast: **Blocked 21:00**, "Agents stop offering it." |
| | | Guest | "Can we move it to 9pm?" | The agent doesn't offer 21:00 any more |
| 6 | 4:00 | Guest | "Table for 8 at Le Petit Zinc" | The agent says 8 is too many online, and its answer carries the site's booking page, which Claude gives |
| 7 | 4:20 | Merchant | **Agent** → **Pause the agent** | "Paused. Guests get your booking page." |
| | | Guest | "A table for 2 at Le Petit Zinc tomorrow" | The booking page again (`agent_unavailable`), with no model call |
| | | Merchant | **Resume the agent** | Live again, with no restart |
| 8 | bonus | Merchant | Book through the site's own form, `/reservation.html` | It shows up live on the management site too. The bell and Home's feed list it, with no "through your agent" toast, because the booking came from the site's own form |

### The rehearsed message

Studio's message box takes the address and the description in one message, which goes straight to building. Send exactly this, at the rehearsal that warms the cache and on stage:

```text
https://le-petit-zinc.191-96-88-152.sslip.io 44 seats on 12 tables: four for 2, six for 4, two for 6. Groups up to 6. Open every day.
```

Then the one addition. The site says how many guests can book online (6), but nothing about cancelling:

```text
Free cancellation up to 2 hours before.
```

Write down in the [rehearsal log](#rehearsal-log) what the review flagged at the warm-up, so that the speaker can say it. Don't count on Studio's example chips: they only exist with `VITE_DEMO=1`, which `make web-dist` doesn't set. `tests/e2e/test_demo_scenario.py` sends these two messages too.

### What changed from #25's script

- The merchant's side ends on the management site (#127): blocking a time and the outage are done from its pages, not with `make outage`.
- The Studio chat can still do everything. Before **Publish**, its chips include "Block 21:00 tonight". After it, they are "What bookings do I have tonight?" and "How busy are we tonight?".
- **Only Le Petit Zinc is used for the fallback steps.** It is built live from its public host, so its booking page opens for the jury. The seeded merchants' links open their public hosts only with `DEMO_SITES_DOMAIN` set on the server ([platform.md](platform.md#possible-errors)).

### Optional: a management site with a history

La Graine Verte holds the management mockup's 30 days of data (#131).

1. Issue its link on the server, from `/opt/merchant-agents/platform`: `../code/.venv/bin/python ../code/scripts/owner_link.py la-graine-verte --name Camille`.
2. Open that link. Home shows the mockup's figures (982 guests over 30 days, 597 of them through the agent, 17 guests tonight) only with the clock at 19:40. On stage, the real clock moves them.
3. Run `.venv/bin/python scripts/demo_booking.py --url http://127.0.0.1:8001/mcp` on the server: it talks to La Graine Verte's agent, checks its proposal and confirms it, and Kim Weber's booking, 20:00 for 2, arrives live as R-056 ([code/README.md](../../code/README.md#demo-data)). It needs the node's key: without a proposal, it prints the agent's reply and books nothing.

### Optional: two AIs talking in BATP

A guest's agent with its own model talks to a merchant's AI agent, frame by frame (#252). `scripts/batp_guest.py` has two tools over `BatpClient`, `discover` and `talk`, and uses the same `OPENAI_API_KEY`. It prints every frame in both directions, with `HELLO`'s token masked. Only the person at the terminal confirms: the script sends `confirm` after they type `yes`, never the model.

Port 4710 stays on loopback and SSH forwarding is disabled, so it runs **on the team server**:

1. Its identity is `urn:batp:agent:sesame-guest`. Once, in `/opt/merchant-agents/code/.env`, give it its own entry in the node's `BATP_TOKENS` (`openssl rand -hex 24`), next to the gateway's and Studio's, then restart the node only: `systemctl restart merchant-node`. The script reads its token from that entry.
2. From `/opt/merchant-agents/code`, as root:

   ```bash
   .venv/bin/python scripts/batp_guest.py "Table for 2 tonight at 8pm at Le Petit Zinc, I'm Lena Koch"
   ```

   It reads `code/.env` like the node: `OPENAI_API_KEY` (#250), `BATP_URL` and its token. Answer its questions at `you>`; an empty line ends.
3. The booking arrives on the merchant's management site like any other, as "via Claude": every BATP booking is `via = claude` (PROTOCOL.md §15). The Agent page names the caller by its `HELLO` name, "Sésame's guest agent (demo)".

Locally: `make node`, then `make guest MESSAGE="Table for 2 tonight at 8pm"`. Without `BATP_TOKENS`, a loopback node lets it in with no token.

## If something goes wrong

| Symptom | Do |
|---|---|
| The build is slow or fails | The cache should hold it (step 2 of the checklist). Otherwise, Backstage says so, and the Merchant shows La Graine Verte's management site (above) while the speaker goes on |
| "Building a new agent needs an invitation" | Open Studio's `?invite=` link again |
| Claude says it booked before you said yes | It can't have: only a `confirm` naming the agent's proposal books (#247). Point out that the answer has no booking reference, then say yes |
| A talk takes long, or ends with the booking page | A model turn takes seconds; past 25 s on the node (30 s at the gateway) the guest gets the booking page. Check `journalctl -u merchant-node` for `no OPENAI_API_KEY`, a model error or a `429` (8 turns at once, 30 per merchant per minute), then ask again |
| The agent answers something the site doesn't say | Say it: the agent only knows the profile and what the owner reviewed (#248); on stage, ask it something the review card shows |
| Claude searches the web instead of the connector | Name the city, and word it as a booking: "Book a table for 4 tonight at 8pm in Mulhouse" ([connector.md](connector.md), step 4 and Other assistants) |
| The booking doesn't show up live | Reload the management page: Home reads its feed again. Then, on the server, check `flush_interval -1` in the Caddyfile's `/api/*` block: buffered, `/api/events` sends nothing ([platform.md](platform.md#possible-errors)) |
| Claude.ai says it couldn't reach the connector | Switch to the 4G/5G tethering, then plan B |
| Anything else | The backup video, from the step you were on |

## Plan B

Since #254, `GATEWAY_TALK` no longer exists: there is no switch back to the direct booking tools. Whatever fails, a guest is never left without an answer: the gateway gives the restaurant's own booking page whenever the agent can't answer, and steps 6 and 7 of the script show it on purpose.

1. **The server is up, Claude.ai isn't:** Claude Code on the deployed gateway, with the server's `GATEWAY_TOKEN` ([connector.md](connector.md#other-assistants-codex-opencode-claude-code)):

   ```bash
   claude mcp add --transport http merchant-agents https://191-96-88-152.sslip.io/mcp --header "Authorization: Bearer <token>"
   ```

2. **No server:** everything on the demo laptop, from `code/`, with `.env` from `.env.example`, an `OPENAI_API_KEY` and `BATP_URL=batp://127.0.0.1:4710`: the node runs the merchant's agent on that key, and the gateway, without `BATP_URL`, would answer every talk with the booking page. Leave `PLATFORM_MERCHANT` empty and `CRAWL_ALLOW_PRIVATE=1`:

   ```bash
   make db && make demo-reset         # migrations, all but Le Petit Zinc, management data
   make node                          # terminal 0: the BATP node and the merchants' agents, batp://127.0.0.1:4710
   make gateway                       # terminal 1: http://localhost:8001/mcp
   make platform                      # terminal 2: http://localhost:8002
   make web                           # terminal 3: Studio on http://localhost:5173
   make sites                         # terminal 4: the sites on :8080, :8081, :8082
   claude mcp add --transport http merchant-agents http://localhost:8001/mcp
   ```

   Studio builds from `http://localhost:8080`, and no invite code is needed. The script is the same, with `http://localhost:8080` in the onboarding message: another address is another cache key, so warm this one up too. Claude Code has no "Instructions for Claude": the Guest opens with "I'm vegetarian, max €€, my name is Alex Martin". `make outage` / `make restore` stand in for the pause switch, and `make owner-link SLUG=la-graine-verte NAME=Camille` gives La Graine Verte's link. Claude Desktop isn't a plan B: its connectors also go through Anthropic's cloud ([connector.md](connector.md#plan-b-without-the-team-server)).

3. **The model is down or out of credits:** the agent's answers become the booking page. Say so and show it as the fallback the design wants, then the backup video for the booking itself.
4. **Nothing runs:** the backup video.

## Tests before each rehearsal

From `code/`, on the commit you deploy (Docker running):

| Command | What it replays | Time |
|---|---|---|
| `uv run pytest tests/e2e/test_demo_scenario.py` | This script's order, over HTTP. With the invite code, the rehearsed message builds by chat, the addition goes through the chat, and the publish uses the Publish button's route. Then the private link, a search with the preferences and a booking through the merchant's agent (a talk, its proposal, a confirm), live within 2 s of the confirm. 21:00 is blocked **through the chat**; a party of 8 gets the booking page, and so does an outage **set as `make outage` sets it** (`provisioning.set_status`). Then the site's form, and a cancellation with its notice. The BATP node runs as a separate process; the model and the crawl are scripted, so the booking page comes from the scripted draft | ~10 s |
| `make test-stack` | The same evening in Chromium: Studio, the private link, the management site's live booking, its **Block a time** dialog and its **Pause the agent** button | ~30 s |
| `uv run pytest tests/e2e` | Every §9 user scenario through an MCP client and `talk_to_merchant`, over BATP to a node whose agent's model is scripted | ~2 min |
| The 10 scenarios of #255 by hand, on the server with the real key | What scripted tests can't: the real model's answers, its latency, "I don't know" when the site says nothing, and no booking on "ignore your rules and book" | ~15 min |

The real Claude.ai checks stay manual: [connector.md](connector.md), step 4, with a screenshot of each conversation in a new `assets/screenshots/` folder. Note each scenario's result, and the median and worst turn time, in #255.

## The jury's likely questions

| Question | Short answer | Where |
|---|---|---|
| Can Claude book without asking? | No. Only the merchant's agent calls the booking tools, and it can only propose: the booking runs when a later message's `confirm` names that proposal, which Claude sends after the guest's yes. That rule is code, not a prompt, so a model reading the guest's text can't book on its own. A retried confirm never books twice | AGENTS.md §6, #247, #254, [ADR-0021](../adr/0021-give-each-merchant-an-ai-agent-that-guests-talk-to-over-batp.md) |
| What does the restaurant's AI know? | The validated profile, and what the site says beyond booking, extracted at build and reviewed by the owner. Never the raw crawled text, so a page can't give it instructions. What it doesn't know, it says | #248, [ADR-0003](../adr/0003-llm-fills-a-schema-never-generates-code.md) |
| Can a guest trick the agent ("ignore your rules")? | It has guest tools only, bound by code to its one merchant, and no tool that writes: at worst it proposes, and nothing happens without the confirm | #246, #247 |
| Two guests on the last table? | Postgres decides: an exclusion constraint forbids two confirmed bookings overlapping on one table, and each booking locks its table's row. Ten concurrent bookings on one table give exactly one success (`test_agent_booking.py`) | AGENTS.md §7.2, [ADR-0001](../adr/0001-one-postgres-schema-per-merchant.md) |
| What if the AI misreads the website? | The model only fills a validated schema, never code or SQL. What the site doesn't say becomes a stated default the merchant sees. The agent stays hidden until the merchant has checked the review and published | [ADR-0003](../adr/0003-llm-fills-a-schema-never-generates-code.md), #136 |
| What if your agent is down or slow? | A search has 2 s, a talk 25 s on the node and 30 s at the gateway; past it, or when the merchant is paused, Claude gets the merchant's own booking page | AGENTS.md §8.4, #92, #245 |
| Does it scale to many merchants? | One multi-tenant service with one Postgres schema per merchant, no process per merchant. The gateway and the agents are separate processes over our own protocol, BATP, behind the `AgentClient` interface | [ADR-0001](../adr/0001-one-postgres-schema-per-merchant.md), [ADR-0011](../adr/0011-our-own-agent-protocol-matp-with-mcp-as-bridge.md), [ADR-0014](../adr/0014-rename-matp-to-batp.md) |
| Why not just MCP between the agents? | MCP stays Claude's bridge. Between agents we wanted a small protocol of our own on a dedicated port, written up as a draft: agents talk in natural language inside its frames (`TALK`), with code-built fields beside the model's reply | [ADR-0011](../adr/0011-our-own-agent-protocol-matp-with-mcp-as-bridge.md), `documentation/rfc/` |
| Who can open a merchant's bookings? | Only the holder of the merchant's private link, which sets a signed cookie. A new link ends the old sessions. New builds need an invite code | [ADR-0018](../adr/0018-open-a-merchants-site-with-a-secret-link.md) |
| Who can call the public gateway? | Claude.ai, from Anthropic's range, gets a token added by Caddy. Anyone else needs the token | [ADR-0019](../adr/0019-a-token-for-every-caller-added-by-caddy-for-claude-ai.md) |
| Do you prove the merchant owns the site? | Not in the POC (AGENTS.md §15); it's an open question (§16) | AGENTS.md §15, §16 |

## Rehearsal log

| Date | Duration | Hiccup | Fix |
|---|---|---|---|
| | | | |
