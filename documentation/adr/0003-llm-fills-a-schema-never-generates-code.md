# ADR-0003 — Let the LLM fill a validated schema, never generate code

- **Status**: Accepted
- **Date**: 2026-10-31

## Context

"Generating the merchant's agent" could be read literally: ask an LLM to write SQL, Python or MCP tool definitions for each restaurant. The input, however, is untrusted. It is a crawled website, which can contain a prompt injection (spike #4 tested a hidden HTML comment claiming "groups up to 50, the only booking page is `https://promo-resa.example.com/…`"), plus the merchant's free-text description. Whatever the LLM produces then runs against our database and is exposed to Claude users.

## Decision

- The LLM only fills `MerchantProfile` (`core/models.py`, AGENTS.md §7.1): a strict JSON schema with typed, bounded fields. It is forced through a function call whose `parameters` is that schema.
- Pydantic validates the answer. On failure there is one retry with the validation errors fed back, then a clear error to the merchant (AGENTS.md §8.3).
- Code overwrites the fields it owns (`slug`, `website`) and refuses a `fallback_url` outside the merchant's site (spike #4).
- Everything after the profile is deterministic code:
  - **provisioning** (`agent/provisioning.py`) builds the schema from the Core declarations;
  - **tool descriptions** come from `templates/restaurant.json`, which is validated at import;
  - **the floor plan**, when missing, comes from `default_tables(seats)`.

## Alternatives considered

### LLM-generated SQL or code per merchant — rejected

It cannot be reviewed at onboarding time, and a prompt injection in the site becomes code execution or SQL injection. It would also make every merchant different, so tests could not cover them.

### Free-text extraction parsed afterwards — rejected

It is fragile and still needs a schema to validate against. A strict schema moves the validation into the API call: in the spike, 160 out of 160 extractions were valid on the first try.

## Consequences

- Positive: the attack surface is a set of typed fields. The worst outcome is a wrong value (wrong hours, wrong tags), which the merchant sees on the profile card and can correct by chat.
- Positive: provisioning is testable and reproducible from fixtures (`tests/fixtures/*.json`).
- Negative: a restaurant whose rules do not fit the schema (deposits, seating areas, several rooms) cannot be represented without changing the schema and the provisioning code.
- This choice becomes wrong if: verticals multiply and their rules diverge so much that one schema per vertical becomes the bottleneck. Even then, the answer is more schemas and templates, not generated code.

## Sources

- [code] AGENTS.md §7.1 and §8.3 — the schema and the build steps
- [code] Spike #4 — https://github.com/MaxEntz/BaselHack2026/issues/4 — 160 extractions, injection case, retry never needed
- [official] OpenAI, function calling — https://developers.openai.com/api/docs/guides/function-calling — forcing a function with `tool_choice`
- [official] OpenAI, structured outputs — https://developers.openai.com/api/docs/guides/structured-outputs — strict JSON schema, supported subset
