# ADR-0021 — Give each merchant an AI agent that guests' agents talk to over BATP

- **Status**: Accepted
- **Date**: 2026-11-01

## Context

Until BATP 0.1, "the merchant's agent" is not an AI ([ARCHITECTURE.md §2](../ARCHITECTURE.md#2-overview)). It is the restaurant's data in its own schema, plus booking tools served by one multi-tenant `AgentService`. Every intent is a structured call. The guest's side decides everything: Claude reads the tool descriptions, picks `get_availability` or `create_reservation` and fills their arguments, and the node runs one handler per intent (`merchant_node/agents.py`).

That has three limits, which #244 sets out to remove:

- **Nobody on the merchant's side understands the restaurant.** "Do you have vegetarian dishes?" or "Is the terrace open in November?" has no intent. The guest's agent can only read the website itself, or give up.
- **Every guest agent needs to know our tools.** A caller that only speaks natural language can't use BATP.
- **The guest's confirmation is a prompt rule.** AGENTS.md §6 says Claude confirms before a write, and only the tool descriptions enforce it (#14, #109). Another caller is "expected to do the same" (PROTOCOL.md §7).

The constraints don't change. Claude.ai only speaks MCP, so the gateway stays a bridge. One multi-tenant process serves every merchant (AGENTS.md §13). The backend's LLM is OpenAI's (ADR-0004), and an LLM never produces code, SQL or tool definitions (ADR-0003). The demo has to keep working at every step of the switch (#244, "If we fall behind").

## Decision

- **Each merchant gets an AI agent**: a language model, run inside the BATP node, that answers guests' agents and is the only caller of that merchant's booking tools. It is still one multi-tenant process; the merchant is bound by code, never by a tool argument, so one merchant's agent can't reach another's data. It has guest tools only, and admin tools stay on the platform.
- **BATP 0.2 adds the `TALK` intent** ([PROTOCOL.md §7](../PROTOCOL.md#7-intents)). The guest's agent sends a message in natural language, with a `conversation` after the first one. The answer is the model's `reply`, plus fields that code builds from tool results, never the model: `slots`, `pending`, `reservation`, `fallback_url`, `tools_called`. `200` means the agent answered, even "we're full". A `0.1` session is neither offered nor served `TALK`.
- **The confirmation moves into code** (#247). The agent can only *propose* a write (`pending`, with an `id` and an expiry). It runs when a later `TALK` names that proposal in `confirm`, which the caller sends only after the guest's explicit yes.
- **What the agent knows** is the validated `MerchantProfile` and site knowledge extracted at build into a validated schema, then reviewed by the owner (#248). Crawled text never enters its prompt (ADR-0003 stands).
- **`TALK` replaces the four addressed intents.** `AVAILABILITY`, `BOOK`, `AMEND` and `CANCEL` and the four MCP booking tools stay until #254 closes them, behind the gateway's `GATEWAY_TALK` switch (#251). `DISCOVER`, `RECALL` and `PING` stay: they address no merchant, so there is no agent to talk to.
- **The gateway has no LLM.** It carries the conversation with one tool, `talk_to_merchant` (#251).
- **Deadlines:** a model turn with its tool calls doesn't fit the 10 s per request (PROTOCOL.md §10) or the gateway's 2 s (AGENTS.md §8.4). `TALK` gets 25 s on the node, and 30 s at the gateway (`AGENT_TALK_TIMEOUT_MS`, #251), so the node's `503` with the fallback URL arrives first.
- **Same key and model** as the builder: `OPENAI_API_KEY`, `OPENAI_MODEL`, now also read by the node (#250).

## Alternatives considered

### LLM on the guest side only (BATP 0.1) — rejected

This is what we have today. It is the cheapest option and has no model on our side, but it leaves the three limits of the context: no answer about the restaurant itself, a caller that must know our tools, and a confirmation that rests on a prompt.

### An extra question intent beside the structured ones — rejected

An `ASK` intent for free questions, with `BOOK` and the others kept as they are. The writes would stay structured calls from the guest's agent, so the confirmation would still rest on that agent's prompt, and every caller would still need our tool schemas. It also gives two ways to book, which the model on the guest side can mix up (the reason `GATEWAY_TALK` never shows both tool sets at once).

### The structured intents behind the agent at once, without a switch — rejected

The direct intents and the four MCP tools would go in the same change. The demo would then depend on the agent from the first merge. The switch (#251) and a separate closing PR (#254) keep `main` demoable at every step.

## Consequences

- Positive: a guest's agent can ask anything the owner reviewed, in natural language, and book in the same conversation. Any agent that speaks BATP can join without knowing our tools.
- Positive: the guest's yes is checked by code. A model reading untrusted text can propose a booking but can't write one.
- Negative: a model call per message. `TALK` is slower (seconds, not milliseconds) and costs per turn. Its answers vary from run to run, so tests script the model and the demo has to be rehearsed (#255).
- Negative: prompt injection now matters on the merchant's side as well. The guest's message is untrusted text inside the agent's prompt. What protects the merchant is in code: guest tools only, the merchant bound by code, writes behind `confirm`, crawled text never in the prompt.
- Negative: the node now holds the OpenAI key and conversation state (#249, #250).
- This choice becomes wrong if: model latency or cost makes conversations unusable for guests, or if a standard agent-to-agent protocol with its own conversation model gains the clients we need (then bridge to it, in a new ADR, as ADR-0011 says).

## Sources

- [code] `code/packages/batp/src/batp/messages.py` (`TALK`, `SINCE_MINOR`, `negotiate`), `code/packages/core/src/core/models/tools.py` (`TalkInput`, `TalkOutput`, `PendingAction`) — the 0.2 frames, #245
- [code] `documentation/PROTOCOL.md` §7 and §14 and the draft in `documentation/rfc/` — the `TALK` rules and how a new intent is a minor version
- [analyse] Issue #244 — https://github.com/MaxEntz/BaselHack2026/issues/244 — the plan, the switch and the checkpoint
- [official] OpenAI, function calling — https://platform.openai.com/docs/guides/function-calling — how the agent's model calls the guest tools with arguments that code validates
