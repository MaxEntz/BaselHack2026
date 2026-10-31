"""public.merchant_owners: who can open a merchant's management site (#132)

One owner per merchant, identified by a secret link shown once in the chat: only the token's
SHA-256 is stored. In `public`, like `reservation_changes`, so existing merchants need no
re-provision (ADR-0001, ADR-0013); deleting the merchant deletes its owner, so a slug used
again never opens for the previous owner.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-31 18:00:00

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("""
        CREATE TABLE public.merchant_owners (
          merchant_slug  text PRIMARY KEY REFERENCES public.merchants (slug) ON DELETE CASCADE,
          owner_name     text,
          token_hash     text NOT NULL UNIQUE,
          created_at     timestamptz NOT NULL DEFAULT now()
        )
    """)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TABLE public.merchant_owners")
