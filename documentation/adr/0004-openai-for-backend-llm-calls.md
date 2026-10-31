# ADR-0004 — Use the OpenAI API for backend LLM calls

- **Status**: Accepted
- **Date**: 2026-10-31

## Context

The backend calls an LLM in two places: the builder's `extract` step (website + description → `MerchantProfile`) and the merchant-side platform assistant (a tool-use loop over the admin tools). The initial plan (AGENTS.md §14, first rows) was the Claude API for both, to keep a single SDK. The team's available API key turned out to be an OpenAI one.

Claude stays the **user-side** client either way: users book from Claude.ai, which calls our MCP gateway. That path involves no API key of ours.

## Decision

- Every backend LLM call uses the **OpenAI Python SDK** with the Responses API. The model comes from `OPENAI_MODEL` (default `gpt-6.1-terra`) and the key from `OPENAI_API_KEY`.
- Extraction uses a **forced strict function call**. The `MerchantProfile` JSON schema goes through `strict_schema()` (from `scripts/spike_extract.py`), because strict mode rejects `format: uri` and optional properties.
- These calls will be isolated in two functions, the builder's `extract` (#22) and the platform's `assistant` (#17), so the provider can be switched.

## Alternatives considered

### Claude API (Anthropic SDK) — rejected for now

This was the original choice, and it is technically equivalent: tool use with an `input_schema`. It was rejected only because no Anthropic key was available to the team. This was a constraint, not a technical judgment.

### A multi-provider abstraction layer — rejected

It would cost 1–2 hours with no visible gain in the demo. Keeping the calls in two functions is enough to switch providers later.

## Consequences

- Positive: spike #4 validated the approach on the OpenAI API: 160 out of 160 valid profiles on the first try, about 4.7 s and $0.0026 per extraction (`gpt-6.1-terra`, effort `none`).
- Negative: the product story ("let Claude book") uses another vendor's model behind the scenes for onboarding and for the merchant assistant.
- Negative: strict mode constrains the schema (no `format`, every property required). `strict_schema()` adapts Pydantic's schema, and Pydantic still validates the answer in full.
- This choice becomes wrong if: an Anthropic key becomes available and we want a single vendor. Then switch the two functions back and write a new ADR that supersedes this one.

## Sources

- [code] Spike #4 results — https://github.com/MaxEntz/BaselHack2026/issues/4 — benchmark by configuration, cost, latency
- [code] PR #30 — https://github.com/MaxEntz/BaselHack2026/pull/30 — the switch: AGENTS.md, `core/config.py`, `.env.example`
- [official] OpenAI, function calling — https://developers.openai.com/api/docs/guides/function-calling
- [official] OpenAI, structured outputs — https://developers.openai.com/api/docs/guides/structured-outputs — supported schema subset in strict mode
