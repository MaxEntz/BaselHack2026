# ADR-0001 — Isolate each merchant in its own Postgres schema

- **Status**: Accepted
- **Date**: 2026-10-31

## Context

Each merchant gets "its own agent" (AGENTS.md §1): its own tables, policy, blocked slots and
reservations. The POC runs a single multi-tenant service (§13), so the isolation has to come from
the data layer. Double booking must be impossible even under concurrent requests, which the
`EXCLUDE USING gist` constraint on `(table_id, during)` guarantees per merchant. Merchants are
created at runtime by the builder, not by a developer, so their storage cannot depend on a
migration being written for each of them.

## Decision

- `public` holds what is shared: the registry (`merchants`), guest notices and the
  `notify_reservation()` trigger function. Only `public` is managed by Alembic (migration `0001`).
- Each merchant gets a schema `m_<slug>` (hyphens → underscores) with the same four tables and a
  `reservation_seq` sequence, created by provisioning (#7).
- The merchant tables are declared **once** in SQLAlchemy Core (`core/db.py`) under the
  placeholder schema `merchant`, together with the `reservations_notify` trigger (attached as
  `after_create` DDL), so `merchant_metadata.create_all` builds a complete merchant schema.
  `merchant_conn(engine, slug)` opens a transaction whose `schema_translate_map` rewrites
  `merchant` to `m_<slug>`.
- `schema_for(slug)` is the only way a slug becomes an identifier: it validates the slug against
  the contract's `Slug` pattern (`^[a-z0-9-]{3,61}$`) and raises `InvalidInput` otherwise.
  `m_` + 61 characters = 63 bytes, the Postgres identifier limit. Slugs contain no `_`, so the
  mapping is injective.

## Alternatives considered

### One shared set of tables with a `merchant_id` column — rejected

Every query and every constraint would need the tenant column; forgetting one `WHERE` leaks
another merchant's bookings. The exclusion constraint would need `merchant_id WITH =` as well.
With one schema per merchant, a query that forgets the merchant fails instead of leaking.

### One database per merchant — rejected

One connection pool per merchant and a database created at runtime, for no gain at demo scale;
the registry used by discovery would also live in another database.

### Row-level security — rejected

Correct isolation, but it relies on a session variable set on every connection and on policies
written for each table: more moving parts to get wrong in a 24-hour build.

## Consequences

- Positive: isolation is structural; the exclusion constraint and the reservation sequence are
  naturally per merchant; dropping a merchant is `DROP SCHEMA … CASCADE`. A `NOT isempty(during)`
  check closes the one gap of the exclusion constraint (an empty range overlaps nothing).
- Negative: sequences ignore rollbacks, so a booking rejected after taking its ID leaves a gap
  in the `R-NNN` numbering.
- Positive: no SQL is built from a slug. Merchant queries are Core constructs, translated by
  SQLAlchemy; the only identifier derived from input goes through `schema_for`.
- Negative: `schema_translate_map` does not rewrite `text()` SQL. Merchant queries must use Core
  (`select(reservations_t)`), and reservation IDs come from Python (`next_reservation_id`)
  rather than from a server default naming the sequence.
- Negative / accepted debt: **merchant schemas have no migrations**. Changing a merchant table
  means dropping and re-provisioning every merchant, which is fine for the POC (fixtures are
  re-seeded by `make demo-reset`) but not for real data.
- Negative: `downgrade` of `0001` fails while merchant schemas exist (their triggers use
  `public.notify_reservation()` and their constraints use `btree_gist`): deprovision first.
- This choice becomes wrong if: we need to change merchant tables while keeping real bookings
  (then add a per-schema migration runner, or move to shared tables), or the number of merchants
  grows into the thousands (catalog size, connection-level translation overhead).

## Sources

- [official] SQLAlchemy — Translation of Schema Names — https://docs.sqlalchemy.org/en/21/core/connections.html#translation-of-schema-names — how `schema_translate_map` rewrites Core statements and DDL, and that it does not apply to `text()`
- [official] PostgreSQL 16 — Schemas — https://www.postgresql.org/docs/16/ddl-schemas.html — namespaces inside one database
- [official] PostgreSQL 16 — Constraints (exclusion constraints) — https://www.postgresql.org/docs/16/ddl-constraints.html — `EXCLUDE USING gist` and why it prevents overlapping ranges
- [official] PostgreSQL 16 — NOTIFY — https://www.postgresql.org/docs/16/sql-notify.html — notifications are delivered at commit only
- [analyse] Michael Nygard — Documenting Architecture Decisions — https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions — the ADR format used here
