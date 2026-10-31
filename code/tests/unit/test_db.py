"""schema_for: the only way a slug becomes a Postgres identifier."""

import pytest

from core.db import make_engine, merchant_conn, schema_for
from core.errors import InvalidInput


@pytest.mark.parametrize(
    ("slug", "schema"),
    [
        ("le-petit-zinc", "m_le_petit_zinc"),
        ("abc", "m_abc"),
        ("2-pieces-cuisine", "m_2_pieces_cuisine"),
        ("a" * 61, "m_" + "a" * 61),
    ],
)
def test_schema_for_maps_the_slug(slug: str, schema: str) -> None:
    assert schema_for(slug) == schema


def test_longest_schema_fits_the_postgres_identifier_limit() -> None:
    assert len(schema_for("a" * 61).encode()) == 63


@pytest.mark.parametrize(
    "slug",
    [
        "",
        "ab",
        "a" * 62,
        "../etc",
        "Le-Petit-Zinc",
        'zinc"; DROP SCHEMA public CASCADE; --',
        "le_petit_zinc",
        "café",
        "abc\n",
        " abc",
        "abc def",
    ],
)
def test_schema_for_rejects_unsafe_slugs(slug: str) -> None:
    with pytest.raises(InvalidInput, match="slug"):
        schema_for(slug)


def test_schema_for_rejects_a_non_string() -> None:
    with pytest.raises(InvalidInput, match="slug"):
        schema_for(None)  # type: ignore[arg-type]


def test_schema_for_raises_the_invalid_input_code() -> None:
    with pytest.raises(InvalidInput) as raised:
        schema_for("../etc")

    assert raised.value.code == "invalid_input"


async def test_merchant_conn_rejects_a_bad_slug_before_connecting() -> None:
    # Nothing listens on port 1: reaching the database would raise OperationalError instead.
    engine = make_engine("postgresql+psycopg://u:p@127.0.0.1:1/db")
    with pytest.raises(InvalidInput):
        async with merchant_conn(engine, "../etc"):
            pass
    await engine.dispose()
