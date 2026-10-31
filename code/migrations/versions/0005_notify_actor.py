"""notify_reservation() also says who made the change: `actor` (#134)

The management site moves bookings (#134). The live listener sends the merchant only its guests'
changes (#81), and an update used to be the guest's by construction, since no merchant tool moved
a booking. Merchant operations now set the transaction-local `sesame.actor` to `merchant`
(`core.db.as_merchant`), and the shared trigger function passes it on. The function lives in
`public`, so existing merchants need no re-provision (ADR-0001).

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-31 20:00:00

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Outside merchant operations `current_setting(…, true)` is NULL, or '' on a connection
    # where a transaction once set it: both are sent as null.
    op.execute("""
        CREATE OR REPLACE FUNCTION public.notify_reservation() RETURNS trigger AS $$
        BEGIN
          PERFORM pg_notify('reservations', json_build_object(
            'schema', TG_TABLE_SCHEMA, 'op', TG_OP, 'id', NEW.id,
            'status', NEW.status, 'via', NEW.via,
            'actor', NULLIF(current_setting('sesame.actor', true), ''))::text);
          RETURN NEW;
        END
        $$ LANGUAGE plpgsql
    """)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("""
        CREATE OR REPLACE FUNCTION public.notify_reservation() RETURNS trigger AS $$
        BEGIN
          PERFORM pg_notify('reservations', json_build_object(
            'schema', TG_TABLE_SCHEMA, 'op', TG_OP, 'id', NEW.id,
            'status', NEW.status, 'via', NEW.via)::text);
          RETURN NEW;
        END
        $$ LANGUAGE plpgsql
    """)
