"""public.merchant_sites: the public site's screenshots, as the crawl read it (#151)

Stored with the build, so that Studio can show them after a reload. In `public`, like
`merchant_owners`, so existing merchants need no re-provision (ADR-0001, ADR-0013): they simply
have none. Deleting the merchant deletes them.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-31 23:30:00

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("""
        CREATE TABLE public.merchant_sites (
          merchant_slug  text PRIMARY KEY REFERENCES public.merchants (slug) ON DELETE CASCADE,
          pages          jsonb NOT NULL,
          created_at     timestamptz NOT NULL DEFAULT now()
        )
    """)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TABLE public.merchant_sites")
