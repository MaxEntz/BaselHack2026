"""Merchant accounts (#132): a secret link opens a merchant's management site.

A build gives its merchant one owner, identified by a random token shown once in the chat as a
link; only the token's SHA-256 is stored (`public.merchant_owners`). Opening the link sets a
signed cookie naming the merchant and a fingerprint of that hash, so that a new link, or the
merchant's deletion, ends every session opened with the old one. Proving that the owner runs
the website stays out of scope (AGENTS.md §15).
"""

import base64
import hashlib
import hmac
import json
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import ColumnElement, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from core.db import merchant_owners_t

OWNER_COOKIE = "sesame_owner"
INVITE_COOKIE = "sesame_invite"
COOKIE_MAX_AGE = timedelta(days=30)
TOKEN_BYTES = 32
# Enough of the token's hash to tell one link from the next; the cookie is signed anyway.
FINGERPRINT_CHARS = 16


@dataclass(frozen=True)
class Owner:
    slug: str
    name: str | None
    token_hash: str

    @property
    def fingerprint(self) -> str:
        return self.token_hash[:FINGERPRINT_CHARS]


class OwnerStore(Protocol):
    async def replace(self, slug: str, token_hash: str, name: str | None) -> None:
        """Make `token_hash` the merchant's only link; any earlier one stops working."""
        ...

    async def by_token_hash(self, token_hash: str) -> Owner | None: ...

    async def by_slug(self, slug: str) -> Owner | None: ...


@dataclass(frozen=True)
class IssuedLink:
    """A new secret link, and the fingerprint the sessions opened with it carry."""

    url: str
    fingerprint: str


def new_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str) -> str:
    # A 256-bit random token needs no slow hash: nobody can guess it to test candidates.
    return hashlib.sha256(token.encode()).hexdigest()


class PgOwnerStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def replace(self, slug: str, token_hash: str, name: str | None) -> None:
        statement = pg_insert(merchant_owners_t).values(
            merchant_slug=slug, token_hash=token_hash, owner_name=name
        )
        updates = {"token_hash": statement.excluded.token_hash}
        if name is not None:  # a new link without a name keeps the owner's
            updates["owner_name"] = statement.excluded.owner_name
        async with self._engine.begin() as conn:
            await conn.execute(
                statement.on_conflict_do_update(
                    index_elements=[merchant_owners_t.c.merchant_slug], set_=updates
                )
            )

    async def by_token_hash(self, token_hash: str) -> Owner | None:
        return await self._one(merchant_owners_t.c.token_hash == token_hash)

    async def by_slug(self, slug: str) -> Owner | None:
        return await self._one(merchant_owners_t.c.merchant_slug == slug)

    async def _one(self, condition: ColumnElement[bool]) -> Owner | None:
        columns = merchant_owners_t.c
        query = select(columns.merchant_slug, columns.owner_name, columns.token_hash).where(
            condition
        )
        async with self._engine.connect() as conn:
            row = (await conn.execute(query)).one_or_none()
        return Owner(row.merchant_slug, row.owner_name, row.token_hash) if row else None


class MemoryOwnerStore:
    """`OwnerStore` in memory: a platform without a database (tests) has no owner to keep."""

    def __init__(self) -> None:
        self._owners: dict[str, Owner] = {}

    async def replace(self, slug: str, token_hash: str, name: str | None) -> None:
        previous = self._owners.get(slug)
        kept = name if name is not None else (previous.name if previous else None)
        self._owners[slug] = Owner(slug, kept, token_hash)

    async def by_token_hash(self, token_hash: str) -> Owner | None:
        return next((o for o in self._owners.values() if o.token_hash == token_hash), None)

    async def by_slug(self, slug: str) -> Owner | None:
        return self._owners.get(slug)

    def forget(self, slug: str) -> None:
        self._owners.pop(slug, None)


def login_url(web_url: str, token: str) -> str:
    """The secret link: the web app's `/api` reaches the platform's `GET /login`."""
    return f"{web_url.rstrip('/')}/api/login?token={token}"


def issued(web_url: str, token: str) -> IssuedLink:
    return IssuedLink(login_url(web_url, token), hash_token(token)[:FINGERPRINT_CHARS])


class Signer:
    """HMAC-SHA256 signed, expiring values for cookies."""

    def __init__(self, secret: bytes, clock: Callable[[], datetime] | None = None) -> None:
        self._secret = secret
        self._clock = clock or (lambda: datetime.now(UTC))

    def sign(self, claims: dict[str, str], max_age: timedelta = COOKIE_MAX_AGE) -> str:
        expires = int((self._clock() + max_age).timestamp())
        payload = _b64(json.dumps({**claims, "exp": expires}, separators=(",", ":")).encode())
        return f"{payload}.{self._mac(payload)}"

    def unsign(self, value: str) -> dict[str, str] | None:
        """The claims of a value this signer made, or None if forged, malformed or expired."""
        payload, _, mac = value.partition(".")
        if not same(mac, self._mac(payload)):
            return None
        try:
            claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        except ValueError:
            return None
        if not isinstance(claims, dict):
            return None
        expires = claims.pop("exp", None)
        if not isinstance(expires, int) or expires <= self._clock().timestamp():
            return None
        if not all(isinstance(v, str) for v in claims.values()):
            return None
        return claims

    def _mac(self, payload: str) -> str:
        return _b64(hmac.new(self._secret, payload.encode(), hashlib.sha256).digest())


class Accounts:
    """Links, owner cookies and the invite cookie, over an `OwnerStore`."""

    def __init__(self, store: OwnerStore, signer: Signer, invite_code: str | None = None) -> None:
        self._store = store
        self._signer = signer
        self._invite = invite_code

    async def issue_link_token(self, slug: str, name: str | None = None) -> str:
        """A new secret token for `slug`'s owner; the previous link stops working."""
        token = new_token()
        await self._store.replace(slug, hash_token(token), name)
        return token

    async def owner_for_token(self, token: str) -> Owner | None:
        return await self._store.by_token_hash(hash_token(token))

    def owner_cookie(self, owner: Owner) -> str:
        return self._signer.sign({"slug": owner.slug, "link": owner.fingerprint})

    async def owner_from_cookie(self, value: str | None) -> Owner | None:
        """The owner a cookie names, if it is genuine, current and its link still valid."""
        claims = self._signer.unsign(value) if value else None
        if claims is None or "slug" not in claims:
            return None
        owner = await self._store.by_slug(claims["slug"])
        if owner is None or not same(owner.fingerprint, claims.get("link", "")):
            return None
        return owner

    async def owner(self, slug: str) -> Owner | None:
        return await self._store.by_slug(slug)

    @property
    def needs_invite(self) -> bool:
        return self._invite is not None

    def accepts_invite(self, code: str) -> bool:
        return self._invite is not None and same(code, self._invite)

    def invite_cookie(self) -> str:
        return self._signer.sign({"invite": self._invite_fingerprint()})

    def invited(self, value: str | None) -> bool:
        """No invite code configured, or a cookie for the current code."""
        if self._invite is None:
            return True
        claims = self._signer.unsign(value) if value else None
        return claims is not None and same(claims.get("invite", ""), self._invite_fingerprint())

    def _invite_fingerprint(self) -> str:
        # Changing PLATFORM_INVITE_CODE ends the invitations given with the old one.
        return hash_token(self._invite or "")[:FINGERPRINT_CHARS]


def same(given: str, expected: str) -> bool:
    """Constant-time comparison; as bytes, since `compare_digest` refuses non-ASCII `str`
    (a forged cookie must be refused, not crash the request)."""
    return hmac.compare_digest(given.encode(), expected.encode())


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()
