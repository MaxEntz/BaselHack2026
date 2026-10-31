"""public.agent_requests: what other agents asked each merchant's agent over BATP (#146)

The node writes one row per answered request addressed to a merchant, with both frames, for the
management site's Agent page. Rows hold guest names: only the merchant's own session may read
them (AGENTS.md §12). Kept 7 days, purged by the node as it writes. In `public`, so existing
merchants need no re-provision (ADR-0013).

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-31 18:00:00

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("""
        CREATE TABLE public.agent_requests (
          id             bigserial PRIMARY KEY,
          merchant_slug  text NOT NULL REFERENCES public.merchants (slug) ON DELETE CASCADE,
          at             timestamptz NOT NULL DEFAULT now(),
          agent_name     text NOT NULL,
          intent         text NOT NULL,
          status         int NOT NULL,
          request        jsonb NOT NULL,
          response       jsonb NOT NULL
        )
    """)
    op.execute(
        "CREATE INDEX agent_requests_merchant_at ON public.agent_requests (merchant_slug, at DESC)"
    )
    op.execute("CREATE INDEX agent_requests_at ON public.agent_requests (at)")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TABLE public.agent_requests")
