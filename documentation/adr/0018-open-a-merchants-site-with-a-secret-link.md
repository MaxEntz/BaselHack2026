# ADR-0018 — Open a merchant's site with a secret link and a signed cookie

- **Status**: Accepted
- **Date**: 2026-10-31

## Context

The platform had no authentication (AGENTS.md §13), which was fine for a local, single-merchant demo. The management site (#127, ADR-0015) changes that:

- `GET /events?merchant=` let anyone follow any slug, and its events hold guest names (AGENTS.md §12).
- The `/merchants/{slug}` routes (#129) expose bookings, guests and the agent's request log.
- Sessions lived in memory. After a restart, or in another browser, the merchant was lost, and `build_agent` then refused its website with `SITE_TAKEN`.
- `POST /chat` starts a crawl and paid OpenAI calls for anyone.

Proving that the person onboarding owns the website stays out of scope (AGENTS.md §15, §16). What is needed is that whoever built a merchant, and only they, can come back to it.

## Decision

- **One owner per merchant, identified by a secret link.** A build creates `public.merchant_owners` (migration `0004`): `merchant_slug` primary key, `owner_name`, `token_hash`, `created_at`. `ON DELETE CASCADE` from the registry, so a deleted merchant, or a slug used again, opens for nobody.
  - The token is 32 random bytes (`secrets.token_urlsafe`). Only its SHA-256 is stored. A 256-bit random value can't be guessed, so it needs no slow hash.
  - The link is `<PLATFORM_WEB_URL>/api/login?token=…`, shown once on the profile card (`ProfileCard.login_url`). It is never part of the tool result, so neither the model nor the chat history holds it.
  - `make owner-link SLUG=…` issues a new link, for merchants built before accounts or a lost link. The old link stops working.
- **The link sets a signed cookie.** `GET /login` checks the token and answers `303` to `/manage/<slug>` with `sesame_owner`.
  - The cookie is `HttpOnly`, `SameSite=Lax` and `Path=/`, lasts 30 days, and is `Secure` when the web app is served over `https`.
  - Its value is the merchant's slug, a fingerprint of the current link's hash and an expiry, signed with HMAC-SHA256 under `PLATFORM_COOKIE_SECRET`.
  - Each request reads the owner row again, so a new link ends every cookie of the old one.
- **Every route that shows guest data needs that cookie.**
  - A FastAPI dependency (`auth.owner_of_path`) guards each `/merchants/{slug}` route: `401` `not_signed_in` without the cookie, `403` `other_merchant` with another merchant's.
  - It runs before the body is validated. Only a body that isn't JSON at all answers `422` first, and that reveals nothing.
  - `/events` checks the same at connect, and again at each event and heartbeat, so an open stream ends with its link.
- **Sessions come back through the account, and act with its current link.** A chat session records the link that attached it: a cookie, or the build that created the merchant. Every message checks that link against `merchant_owners`. A replaced link, or a deleted merchant, forgets the session and its history, which holds guest names. Another merchant's cookie is refused. There is no shared default session outside development. History stays in memory: what has to survive a restart is the merchant, not the conversation.
- **The building browser claims its cookie.** A build happens inside a chat stream, which can't set a cookie. The web then calls `POST /session/claim`, honoured only for a session a build attached in this process, while its link is current. The session id is then worth the link it was given with.
- **An invite code gates new builds.** With `PLATFORM_INVITE_CODE` set, a session that works on no merchant needs the invite cookie before any model call (`403` `needs_invite`). `POST /invite` sets that cookie, and the web sends it from `/?invite=<code>`. A new code ends the old invitations. Without the variable, as in local development, nothing is asked.
- **`PLATFORM_MERCHANT` stays, for local development only.** It stands in for the owner of that merchant, so `VITE_MERCHANT_SLUG` demos keep working. The platform refuses to start when it comes with what only a deployment sets: an https web app, a cookie secret or an invite code. Secrets have minimum lengths: 32 characters for the cookie key, 8 for the code, which `POST /invite` lets anyone try without a throttle.
- **No token in the logs.** The `login` log line has the merchant and the outcome. A filter masks `token=` in uvicorn's access log.

## Alternatives considered

### OAuth or magic links by e-mail — rejected

These prove who someone is, not that they own the website, which is the open question of §16. They need an identity provider or an e-mail service, and the merchant has no e-mail field. The link gives the same "only whoever built it" guarantee with neither.

### Server-side sessions in a table — rejected

A session table needs a write per login and a purge. The signed cookie holds all the session needs: the merchant and the link it came from. One read of `merchant_owners` per request also gives revocation.

### `itsdangerous` or JWT for the cookie — rejected

Both add a dependency or a format with more options than needed: algorithm choice is a known JWT pitfall. The stdlib `hmac` with `compare_digest` covers one fixed algorithm in about forty lines, and the tests cover forged, expired, other-key and revoked values.

### The token in the URL fragment, so the server never sees it — rejected for now

A fragment never reaches the server or the `Referer`. It needs a web page that reads the fragment and posts it, and the issue asks for a plain `GET /login` link. The access-log filter covers our own logs. Caddy keeps no access log: `deploy/Caddyfile` has no `log` directive, and the site of #142 must keep it that way. Revisit if a proxy that logs query strings is added.

## Consequences

- **Positive:**
  - A browser without the link can't read a guest's name anywhere on the platform.
  - A merchant who comes back with the link finds their site, after a restart or in another browser.
  - Strangers can't spend the OpenAI key when an invite code is set.
- **Negative, accepted debt:**
  - Anyone holding the link is the owner. The card says so, and a new link revokes it: cookies, chat sessions and open streams alike.
  - `POST /logout` ends this browser's cookie only.
  - `owner_name` stays empty until a step asks for it. The pages greet without a name meanwhile.
  - Without `PLATFORM_COOKIE_SECRET`, cookies end with the process. The server must set it, and #142 checks it before deploying.
- **This becomes the wrong choice if:**
  - a merchant needs several people with different rights;
  - ownership of the website must be proven;
  - a proxy in front of the platform logs query strings. Then move the token to a fragment and a `POST`.

## Sources

- [officiel] OWASP Session Management Cheat Sheet — https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html — cookie attributes (`HttpOnly`, `Secure`, `SameSite`) and session IDs of at least 64 bits of entropy.
- [officiel] MDN, `Set-Cookie` — https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Set-Cookie — what `SameSite=Lax` sends on top-level navigations and withholds on cross-site requests.
- [spec] RFC 2104, HMAC — https://datatracker.ietf.org/doc/html/rfc2104 — the keyed hash that signs the cookie.
- [officiel] Python `hmac` and `secrets` — https://docs.python.org/3/library/hmac.html, https://docs.python.org/3/library/secrets.html — `compare_digest` against timing attacks; `token_urlsafe` for the link.
