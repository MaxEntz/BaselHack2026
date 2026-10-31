# ADR-0005 — Name the merchant platform package `merchant_platform`, not `platform`

- **Status**: Accepted
- **Date**: 2026-10-31

## Context

The first spec (AGENTS.md §4) named the merchant-side API package `platform`. Python's standard library already has a `platform` module. Libraries we depend on import it, for example uvicorn, pydantic, SQLAlchemy and the OpenAI SDK, which read the OS and the interpreter version. With a first-party package of the same name on the path, `import platform` resolves to one or the other depending on `sys.path` order. The resulting failures are confusing and far from the cause.

## Decision

The package is `merchant_platform` (`code/packages/merchant_platform/src/merchant_platform/`), run with `uv run uvicorn merchant_platform.api:app --port 8002`. A smoke test (`tests/unit/test_smoke.py`) asserts that `import platform` still resolves to the standard library.

## Alternatives considered

### Keep `platform` and rely on path order — rejected

It works or breaks depending on how the process is started (uvicorn, pytest, `python -m`), so it is hard to debug during a hackathon.

### `app` or `api` — rejected

Too generic for a workspace with five packages. `merchant_platform` says which side of the product it serves.

## Consequences

- Positive: no shadowing, and the smoke test guards it.
- Negative: the name differs from the original spec. AGENTS.md §0 records the correction.
- This choice becomes wrong if: never, as long as the stdlib keeps a `platform` module.

## Sources

- [official] Python standard library, `platform` — https://docs.python.org/3/library/platform.html — the module that would be shadowed
- [code] AGENTS.md §0 — the correction; `code/tests/unit/test_smoke.py` — `test_stdlib_platform_is_not_shadowed`
