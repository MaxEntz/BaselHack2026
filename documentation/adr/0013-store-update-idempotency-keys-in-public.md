# ADR-0013 — Store the idempotency keys of applied updates in `public`, not in each merchant schema

- **Status**: Accepted
- **Date**: 2026-10-31

## Context

`update_reservation` takes an `idempotency_key` (invariant 2 of `ARCHITECTURE.md`; the MATP draft's AMEND), but the service only logged it (#78). A late retry of an older change was applied again. For example, k1 moves to 20:00 and k2 to 21:00, then k1 arrives again (the gateway's 2 s timeout, then Claude retrying): the booking went back to 20:00 without any error. The same key with other parameters was also accepted, whereas a creation answers `invalid_input` for that.

Fixing it means storing, for each applied update, its key and its parameters, written in the same transaction as the update. Creations already keep their key in the merchant's own `reservations` table.

ADR-0001 isolates each merchant in its own schema, created from the Core declarations by provisioning, with no migrations: a merchant-schema change means drop and re-provision. Merchants are already live on the team server, with their bookings. That is the case ADR-0001 names as the one where its "no migrations" stops holding.

## Decision

- **A table `public.reservation_changes`**, added by Alembic migration `0002`: `(merchant_slug, idempotency_key)` primary key, `reservation_id`, the requested `starts_at` and `party_size` (either may be null), `created_at`. `merchant_slug` references the registry with `ON DELETE CASCADE`, so `deprovision` removes it, as for `guest_notices`.
- **The key is claimed first**, in the update's `merchant_conn` transaction, after the booking's row lock (`_own_booking`): `INSERT … ON CONFLICT DO NOTHING`.
  - A new key goes on to the update. If the update fails, the transaction rolls back and the key with it.
  - A key already applied to the same booking with the same parameters returns the booking as it is now, without changing it.
  - Anything else is `invalid_input`, with the message creations use.
- The row lock queues two concurrent uses of one key on one booking. The second sees the first's committed claim and replays.

## Alternatives considered

### A `reservation_changes` table in each merchant schema — rejected

It was the issue's suggestion, and it would follow ADR-0001's isolation. But nothing adds a table to an existing merchant schema: ADR-0001 has no migrations for them. `merchant_metadata.create_all(checkfirst=True)` on each schema could, but no deploy step runs it, and writing that runner is the start of merchant-schema migrations, a decision of its own. Without it, every live merchant on the server would need a drop and re-provision, which loses its bookings, and until then every update would fail with "relation does not exist". `public` already holds per-merchant rows keyed by slug (`guest_notices`), written in the same transaction as the merchant's own tables.

### Keep the last key on the `reservations` row — rejected

It is a merchant-schema change too. It would also only recognise the latest key, so the late k1 of the example would still be applied.

### Reword the invariant: updates are not idempotent — rejected

The gateway's description tells Claude to reuse the key to retry the same change, and the timeout message tells it to retry. Without stored keys, that advice is what undoes a newer change.

## Consequences

- Positive: invariant 2 holds for updates in both modes, since MATP's AMEND calls the same service. Deploying is `make deploy` (it applies the migrations), with no re-provision.
- Positive: a failed update doesn't use up its key: the guest can retry it once the cause is gone.
- Negative: a merchant's data now lives in two schemas, its own and three tables of `public`. Deprovisioning relies on the cascade from the registry row (tested).
- Negative: this is the shared-table-with-`merchant_slug` pattern ADR-0001 rejected for bookings. Here every query filters on the slug, and a mistake would only reveal whether a key exists, not a booking.
- Negative: update keys and creation keys are separate namespaces, so a creation key reused for an update is a new update.
- Negative: rows are never pruned. That is fine for the POC; past it, prune keys older than a day.
- This choice becomes wrong if: merchant schemas get migrations of their own, which would make the merchant schema the natural place.

## Sources

- [code] `code/packages/agent/src/agent/service.py` (`_claim_update_key`), `code/migrations/versions/0002_reservation_changes.py`
- [official] PostgreSQL `INSERT … ON CONFLICT` — https://www.postgresql.org/docs/16/sql-insert.html#SQL-ON-CONFLICT
- [issue] #78
