# ADR-0007 — Cache builder results so the demo can be replayed offline

- **Status**: Accepted (implementation: #22)
- **Date**: 2026-10-31

## Context

The onboarding moment of the demo is a live build: crawl the site, extract with the LLM, validate, provision, deploy. Two of those steps depend on things we do not control on stage: the hackathon Wi-Fi and the LLM API (latency, rate limits, an occasional different answer). A failed or slow build in front of the jury loses the demo's main point. The "never cut" list (#26) includes "onboarding by chat (at worst served from the builder cache)".

## Decision

- The builder caches the result of the network-bound steps (crawl, extraction), keyed by their inputs, under `code/.cache/builder/` (git-ignored). Cache use is logged, so we know whether a demo ran for real.
- `BUILDER_CACHE` (`core/config.py`) selects the mode:
  - `read_write` (default): use a cached result if present, otherwise call and store;
  - `read_only`: rehearsals and the demo. Use the cache and never write to it, so it stays the rehearsed version. On a miss, call live and log the miss;
  - `off`: always call, for development.
- Steps are still emitted to the chat in the same order with real durations, so a cached build looks the same and stays honest about what happened.

## Alternatives considered

### Always build live — rejected

The demo would depend on the Wi-Fi and on the API. The backup video (#25) covers a total failure, not a slow one.

### Pre-provision the merchant and fake the build steps — rejected

It is no longer a demo of the builder. A cached extraction still goes through real validation, provisioning and deployment.

## Consequences

- Positive: the demo is replayable and fast, and works offline once rehearsed.
- Negative: a cached answer can hide a regression in prompts or extraction. Rehearse once with `off`.
- This choice becomes wrong if: it reaches production use, where a cache keyed by input could serve stale data about a changed website.

## Sources

- [code] `code/packages/core/src/core/config.py` — `builder_cache: Literal["read_write", "read_only", "off"]`
- [code] Issue #22 — https://github.com/MaxEntz/BaselHack2026/issues/22 — builder with cache; issue #26 — "never cut" list
