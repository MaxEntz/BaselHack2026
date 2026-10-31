"""public.merchant_activity: what changed at each merchant, for Home's feed (#135)

Every change, whichever path made it (an agent, the website, the assistant, the management site),
written in the same transaction as the change. `details` holds what the line is worded from at
read time (guest, party, start; times; the form saved), so that "today" stays right the next day.
Holds guest names: only the merchant's owner reads it. In `public`, like the other
per-merchant logs (ADR-0001, ADR-0013).

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-31 22:00:00

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("""
        CREATE TABLE public.merchant_activity (
          id              bigserial PRIMARY KEY,
          merchant_slug   text NOT NULL REFERENCES public.merchants (slug) ON DELETE CASCADE,
          at              timestamptz NOT NULL DEFAULT now(),
          kind            text NOT NULL CHECK (kind IN ('new_booking', 'moved', 'cancelled',
                            'blocked', 'reopened', 'saved', 'paused', 'resumed')),
          source          text NOT NULL CHECK (source IN ('claude', 'merchant', 'website')),
          reservation_id  text,
          details         jsonb NOT NULL DEFAULT '{}'
        )
    """)
    op.execute(
        "CREATE INDEX merchant_activity_latest ON public.merchant_activity (merchant_slug, at DESC)"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TABLE public.merchant_activity")
