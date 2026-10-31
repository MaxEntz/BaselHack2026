"""public.agent_requests: the frames as the text that was on the wire (#146)

`jsonb` re-serializes what it stores (key order, spacing), and the Agent page shows the frames
as sent, never re-serialized (#129, CONTRACTS.md): `request` and `response` become `text`.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-31 19:00:00

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("""
        ALTER TABLE public.agent_requests
          ALTER COLUMN request TYPE text USING request::text,
          ALTER COLUMN response TYPE text USING response::text
    """)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("""
        ALTER TABLE public.agent_requests
          ALTER COLUMN request TYPE jsonb USING request::jsonb,
          ALTER COLUMN response TYPE jsonb USING response::jsonb
    """)
