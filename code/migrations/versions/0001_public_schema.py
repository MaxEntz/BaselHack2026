"""public schema: merchant registry, guest notices, reservation notify function

Only `public` is managed by Alembic. Merchant schemas (`m_<slug>`) are created by
provisioning from the Core declarations in `core.db`.

Revision ID: 0001
Revises:
Create Date: 2026-10-31 03:55:21.571128

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Needed by the EXCLUDE constraint (int =, tstzrange &&) of every merchant schema.
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    op.execute("""
        CREATE TABLE public.merchants (
          id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          slug          text UNIQUE NOT NULL CHECK (slug ~ '^[a-z0-9-]{3,61}$'),
          name          text NOT NULL,
          website       text NOT NULL,
          fallback_url  text NOT NULL,
          city          text,
          tags          text[] NOT NULL DEFAULT '{}',
          price_level   text NOT NULL CHECK (price_level IN ('€', '€€', '€€€', '€€€€')),
          status        text NOT NULL DEFAULT 'building'
                        CHECK (status IN ('building', 'live', 'down')),
          profile       jsonb NOT NULL,
          created_at    timestamptz NOT NULL DEFAULT now()
        )
    """)

    op.execute("""
        CREATE TABLE public.guest_notices (
          id              bigserial PRIMARY KEY,
          guest_name      text NOT NULL,
          merchant_slug   text NOT NULL REFERENCES public.merchants (slug) ON DELETE CASCADE,
          reservation_id  text NOT NULL,
          message         text NOT NULL,
          created_at      timestamptz NOT NULL DEFAULT now(),
          delivered_at    timestamptz
        )
    """)
    op.execute("""
        CREATE INDEX guest_notices_pending ON public.guest_notices (lower(guest_name))
        WHERE delivered_at IS NULL
    """)

    # One function shared by the reservations trigger of every merchant schema.
    # `via` lets the platform filter notifications without re-reading the row.
    op.execute("""
        CREATE FUNCTION public.notify_reservation() RETURNS trigger AS $$
        BEGIN
          PERFORM pg_notify('reservations', json_build_object(
            'schema', TG_TABLE_SCHEMA, 'op', TG_OP, 'id', NEW.id,
            'status', NEW.status, 'via', NEW.via)::text);
          RETURN NEW;
        END
        $$ LANGUAGE plpgsql
    """)


def downgrade() -> None:
    """Downgrade schema.

    Fails while merchant schemas still use the function or btree_gist: deprovision them first.
    """
    op.execute("DROP FUNCTION public.notify_reservation()")
    op.execute("DROP TABLE public.guest_notices")
    op.execute("DROP TABLE public.merchants")
    op.execute("DROP EXTENSION btree_gist")
