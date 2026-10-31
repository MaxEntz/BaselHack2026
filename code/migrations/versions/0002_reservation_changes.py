"""public.reservation_changes: the updates guests applied, by idempotency key (#78)

`update_reservation` claims its key here, in the same transaction as the update, so a late
retry of an older change can't undo a newer one. In `public` rather than in each merchant
schema, so that existing merchants need no re-provision (ADR-0013).

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-31 12:00:00

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("""
        CREATE TABLE public.reservation_changes (
          merchant_slug    text NOT NULL REFERENCES public.merchants (slug) ON DELETE CASCADE,
          idempotency_key  text NOT NULL,
          reservation_id   text NOT NULL,
          starts_at        timestamptz,
          party_size       int CHECK (party_size > 0),
          created_at       timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (merchant_slug, idempotency_key)
        )
    """)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TABLE public.reservation_changes")
