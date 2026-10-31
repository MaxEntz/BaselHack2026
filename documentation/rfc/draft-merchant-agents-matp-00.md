---
title: Merchant Agent Transfer Protocol (MATP)
abbrev: MATP
docname: draft-merchant-agents-matp-00
category: exp
submissiontype: independent
ipr: trust200902
area: Applications
keyword:
  - agents
  - booking
  - MCP
date: 2026-10-31
v: 3

author:
  -
    org: Sésame (BaselHack 2026)
    orgascii: Sesame (BaselHack 2026)
    uri: https://github.com/MaxEntz/BaselHack2026

normative:
  RFC2119:
  RFC8174:
  RFC3339:
  RFC3629:
  RFC3986:
  RFC5234:
  RFC8259:
  RFC9846:
  RFC9293:

informative:
  RFC6335:
  RFC7595:
  RFC8141:
  I-D.hood-independent-agtp:
  I-D.bhutton-json-schema:
  MCP:
    title: Model Context Protocol Specification
    target: https://modelcontextprotocol.io/specification
    author:
      - org: Model Context Protocol
    date: false
  MATP-SCHEMAS:
    title: MATP JSON Schemas (generated from the reference implementation)
    target: https://github.com/MaxEntz/BaselHack2026/tree/main/documentation/rfc/schemas
    author:
      - org: Sésame (BaselHack 2026)
        orgascii: Sesame (BaselHack 2026)
    date: 2026-10-31

--- abstract

This document specifies the Merchant Agent Transfer Protocol (MATP), an
application protocol reserved for software agents that book with merchants
on behalf of people or businesses. A MATP session runs over TCP, starts with
a handshake that names the calling agent, and carries newline-delimited JSON
frames. Each request states an intent (discover merchants, check
availability, book, amend, cancel, recall bookings, ping) and the merchant it
addresses. Responses carry numeric status codes and, when the merchant's own
agent cannot serve the request, a fallback URL to the merchant's website.
MATP is designed to sit behind a Model Context Protocol (MCP) server, which
acts as a bridge for assistants that only speak MCP.

--- middle

# Introduction

Software agents increasingly act for people: they search for a restaurant,
check a table and book it. When that traffic uses HTTP, a merchant cannot
tell it apart from a browser, so it cannot identify the calling agent,
route agent traffic separately, or apply rules meant for agents.

MATP gives that traffic its own channel. Every frame says which agent is
talking (established once per session), what it intends to do, and to which
merchant. The protocol is deliberately small: it covers what a network of
merchant agents needs to take bookings, and nothing else.

## Scope

This version covers the guest side of booking with restaurants: discovery,
availability, booking, amending, cancelling and recalling a guest's
bookings. Merchant-side operations (listing a day's bookings, blocking a
slot, changing a policy) are out of scope: they stay on the merchant's own
platform.

## Relation to MCP

Assistants such as Claude connect to remote tools through MCP {{MCP}}. An
MCP server can act as a bridge: each of its tools maps to one MATP intent
({{mcp-bridge}}). Any other agent can speak MATP directly.

## Relation to AGTP

The idea of an application protocol reserved for agent traffic, with agent
identity and intent-named methods, comes from the AGTP Internet-Draft
{{I-D.hood-independent-agtp}}. MATP is independent from it: it does not
implement AGTP, it uses a different transport framing and method set, and
it is limited to the booking use case.

# Conventions and Definitions

{::boilerplate bcp14-tagged}

This document uses the Augmented Backus-Naur Form (ABNF) notation of
{{RFC5234}}. JSON terminology ("object", "member", "string") is that of
{{RFC8259}}.

Node:
: A MATP server. It serves the merchant agents of one backend.

Merchant agent:
: One merchant's booking logic and data, served by a node and addressed by
  the merchant's slug.

Caller:
: An agent that opens a session with a node: a user's assistant, a
  merchant's agent, or a bridge acting for one of them.

Session:
: One transport connection between a caller and a node, from the caller's
  `hello` to the connection's close.

Frame:
: One JSON object followed by a line feed ({{framing}}).

Intent:
: What a request asks the node to do, for example `BOOK`.

Slug:
: The stable identifier of a merchant on a node: 3 to 61 characters among
  lowercase ASCII letters, digits and "-".

Guest:
: The person a booking is for, identified in this version by the name the
  caller gives (see {{privacy}}).

# Protocol Overview

A caller opens a TCP connection to a node and sends `hello`, naming itself.
The node answers `welcome`, which opens the session, or `reject`, which
closes it. The caller then sends requests. Each request carries an `id`, an
intent, the addressed merchant when the intent needs one, and parameters.
The node answers each request with exactly one response carrying the same
`id`. Several requests may be in flight at once, and responses may arrive
in any order.

~~~ ascii-art
  user's        +-------------+   MCP over HTTPS   +-------------+
  assistant --->| MCP client  |------------------->| MCP gateway |
                +-------------+                    |  (bridge)   |
                                                   +------+------+
                                                          | MATP
  other agent ------------------- MATP --------------+    | TCP 4710
                                                     v    v
                                                   +-------------+
                                                   |  MATP node  |
                                                   +------+------+
                                                          |
                                         merchant agents (one per slug)
~~~
{: title="MATP deployment with an MCP bridge"}

~~~ ascii-art
  Caller                                        Node
    |  hello {agent, token, matp: "0.1"}         |
    |------------------------------------------->|
    |  welcome {node, intents, session}          |
    |<-------------------------------------------|
    |  request {id: "r-1", intent: AVAILABILITY} |
    |------------------------------------------->|
    |  request {id: "r-2", intent: DISCOVER}     |
    |------------------------------------------->|
    |  response {id: "r-2", status: 200}         |
    |<-------------------------------------------|
    |  response {id: "r-1", status: 200}         |
    |<-------------------------------------------|
~~~
{: title="A session with two requests answered out of order"}

# Transport {#transport}

MATP runs over TCP {{RFC9293}}. A node listens on a configured port. This
document uses port 4710 for its examples and its reference implementation;
that port is not registered for MATP ({{iana}}), so a caller MUST be able to
use another port.

On the loopback interface, a session MAY use plain TCP. On any other path,
the caller and the node MUST use TLS 1.3 {{RFC9846}} or a later version,
and the caller MUST authenticate the node's certificate against the host
name it connected to. TLS is set up before the first frame.

Either side MAY close the connection at any time. Closing the connection
ends the session. A request in flight when the session ends has no
response, and the caller MUST NOT assume it was or was not carried out
(see {{idempotency}} for retrying writes safely).

## Addressing

A merchant agent is named by a URI {{RFC3986}} of the form:

~~~ abnf
matp-URI = "matp://" host [ ":" port ] "/" slug
slug     = 3*61( %x61-7A / DIGIT / "-" )   ; a-z, 0-9, "-"
~~~

where `host` and `port` are those of {{RFC3986}}. When `port` is absent, it
is 4710. For example, `matp://node.example/le-petit-zinc` names the merchant
agent `le-petit-zinc` on the node at `node.example`. The slug is not sent in
the handshake: a session reaches every merchant agent of a node, and each
request names its merchant ({{requests}}).

# Framing {#framing}

Every message is a frame: one JSON object {{RFC8259}}, encoded in UTF-8
{{RFC3629}}, followed by one line feed (%x0A).

~~~ abnf
frame = object LF
LF    = %x0A
~~~

The rule `object`, and the rules it uses, are those of {{RFC8259}},
Sections 2 to 7, with one change: inside a frame, the rule `ws` is

~~~ abnf
ws = *( %x20 / %x09 )   ; space or tab, never LF or CR
~~~

so that a frame never contains a line feed before its end. A line feed
inside a string is escaped as `\n`, as JSON requires.

- A frame, without its final LF, MUST NOT exceed 65536 octets. A node that
  has read 65536 octets of a frame without finding its LF MUST send an
  `error` frame with status 413 ({{error-frames}}) and close the session,
  without parsing the frame.
- The object MUST have a member `type` whose value is a string. The types
  defined in this version are `hello`, `welcome`, `reject`, `request`,
  `response` and `error`. The type `event` is reserved ({{events}}).
- A sender MUST NOT generate an object with two members of the same name.
  A receiver MAY treat such a frame as invalid.
- A receiver MUST ignore members of a frame that this document does not
  define for its type ({{versioning}}). This rule applies to the frame
  object itself, not to `params` ({{requests}}).
- An empty line (an LF alone) is not a frame. A node MUST ignore it.

# Session {#session}

The caller speaks first. Its first frame MUST be a `hello`. Until the node
has sent `welcome`, the caller MUST NOT send any other frame, and a node
that receives one MUST answer `reject` with status 400 and close the
session.

## hello

| Member | Type | Requirement |
|---|---|---|
| `type` | `"hello"` | REQUIRED |
| `matp` | version string ({{versioning}}) | REQUIRED |
| `agent` | object, below | REQUIRED |
| `token` | string | see below |
{: title="Members of hello"}

| Member of `agent` | Type | Requirement |
|---|---|---|
| `id` | string, 1 to 256 characters | REQUIRED |
| `name` | string | OPTIONAL, for display and logs |
| `kind` | `"user-agent"` or `"merchant-agent"` | REQUIRED |
| `operator` | string | OPTIONAL, who runs the agent |
{: title="Members of hello.agent"}

- `agent.id` names the calling agent. The node treats it as an opaque
  string and attributes every request of the session to it. This document
  uses identifiers of the form `urn:matp:agent:<name>`; `matp` is not a
  registered URN namespace {{RFC8141}}, so these identifiers are only
  meaningful between parties that agreed on them.
- `agent.kind` is `user-agent` when the caller acts for a guest, and
  `merchant-agent` when it acts for a business.
- `token` is the caller's credential on this node, issued out of band. A
  node MUST refuse a `hello` from a non-loopback peer that has no `token`.
  A node MAY accept a `hello` without `token` on the loopback interface.

The node MUST answer a `hello` with exactly one `welcome` or `reject`.

## welcome

| Member | Type | Requirement |
|---|---|---|
| `type` | `"welcome"` | REQUIRED |
| `matp` | the version the node will use for this session | REQUIRED |
| `node` | object with `id` (string, REQUIRED) and `name` (string, OPTIONAL) | REQUIRED |
| `intents` | array of intent names the node serves | REQUIRED |
| `session` | string, the node's identifier for this session | REQUIRED |
{: title="Members of welcome"}

A caller MUST NOT send a request whose intent is not in `intents`. A node
that receives one MUST answer it with status 400.

## reject

| Member | Type | Requirement |
|---|---|---|
| `type` | `"reject"` | REQUIRED |
| `status` | 400, 401 or 426 | REQUIRED |
| `message` | string, for humans | REQUIRED |
| `matp` | the version the node speaks | OPTIONAL |
{: title="Members of reject"}

- 401: the agent is unknown to the node, the token is missing where it is
  required, or the token does not match the agent.
- 426: the node does not speak the major version in `hello.matp`
  ({{versioning}}). The node SHOULD include `matp`.
- 400: the first frame was not a valid `hello`.

After sending `reject`, the node MUST close the connection.

# Requests and Responses {#requests}

## request

| Member | Type | Requirement |
|---|---|---|
| `type` | `"request"` | REQUIRED |
| `id` | string, 1 to 64 characters | REQUIRED |
| `intent` | intent name ({{intents}}) | REQUIRED |
| `merchant` | slug | REQUIRED for AVAILABILITY, BOOK, AMEND and CANCEL; absent otherwise |
| `params` | object | REQUIRED (`{}` when the intent takes none) |
{: title="Members of request"}

- The caller chooses `id`. It MUST be unique among the requests of the
  session that have not been answered yet. A node that receives a request
  whose `id` is already in flight MUST send an `error` frame with status
  400 and MUST NOT carry out the request.
- `params` MUST validate against the intent's params schema
  ({{json-schemas}}). Unlike frame members, unknown members of `params` are
  invalid: a node MUST answer status 400 with code `invalid_input`.
- A request for an intent that needs `merchant` and lacks it, or for an
  intent that takes none and has it, MUST be answered with status 400.

## response

| Member | Type | Requirement |
|---|---|---|
| `type` | `"response"` | REQUIRED |
| `id` | the request's `id` | REQUIRED |
| `status` | status code ({{status-codes}}) | REQUIRED |
| `result` | object | present if and only if `status` is 200 |
| `error` | object, below | present if and only if `status` is not 200 |
{: title="Members of response"}

| Member of `error` | Type | Requirement |
|---|---|---|
| `code` | error code ({{status-codes}}) | REQUIRED |
| `message` | string, for the guest or the caller | REQUIRED |
| `fallback_url` | absolute URI | REQUIRED for `party_too_large` and `agent_unavailable`; absent otherwise |
{: title="Members of response.error"}

- A node MUST send exactly one response to each request it accepted, unless
  the session ends first.
- `result` MUST validate against the intent's result schema
  ({{json-schemas}}).
- `fallback_url` is the merchant's own booking page. A caller SHOULD give
  it to the guest, so that the guest can still book when the merchant
  agent cannot serve the request.
- `message` MUST NOT contain stack traces or internal identifiers
  ({{security}}).

## Concurrency

A caller MAY send a request before earlier ones are answered. A node MAY
carry out the requests of one session concurrently, and MAY answer them in
any order. A caller MUST match responses to requests by `id` only.

A node MUST accept at least 32 requests in flight per session. A node MAY
answer a request beyond its limit with status 429 and code
`too_many_requests`, without carrying it out. The request itself is
valid: the caller MAY send it again once one of its earlier requests has
been answered.

## Idle sessions

A node SHOULD close a session that has received no frame for 60 seconds,
and MUST NOT close it for idleness sooner. A caller that wants to keep a
session open sends `PING` ({{ping}}).

## Session errors {#error-frames}

An `error` frame reports a problem that no response can carry because no
request `id` can be attributed: the frame is not JSON, has no valid `type`
or `id`, is too large, or reuses an `id` in flight.

| Member | Type | Requirement |
|---|---|---|
| `type` | `"error"` | REQUIRED |
| `status` | 400 or 413 | REQUIRED |
| `message` | string | REQUIRED |
{: title="Members of error"}

After an `error` frame with status 413, the node closes the session. After
one with status 400, the session stays open. A node that receives a frame
of an unknown `type` MUST send an `error` frame with status 400. A caller
that receives a frame of an unknown `type` MUST ignore it, so that later
versions can add frames sent by the node ({{events}}).

## Idempotency {#idempotency}

A write sent again after a lost session must not book twice.

- BOOK and AMEND carry `params.idempotency_key`, chosen by the caller. A
  caller retrying a write MUST reuse the key of the first attempt, with the
  same params, and SHOULD use a fresh unpredictable key for every new write.
- For BOOK, the key is scoped to the merchant. A node that receives a BOOK
  with a key already used at that merchant, and the same `starts_at`,
  `party_size` and guest, MUST return status 200 with the reservation the
  first request created, and MUST NOT create another. If the params differ,
  it MUST answer status 400 with code `invalid_input`.
- An AMEND sent again with the same params MUST leave the booking as the
  first one left it, and return it.
- CANCEL is idempotent without a key: cancelling a booking already
  cancelled by the guest returns it again, with status 200.

## Confirmation before writes

BOOK, AMEND and CANCEL change a guest's plans. A caller acting for a guest
MUST obtain the guest's explicit confirmation of the merchant, time, party
size and name before sending any of them. A node cannot verify this; it is
a requirement on callers, and a bridge enforces it through what it tells
the assistant ({{mcp-bridge}}).

# Intents {#intents}

Times are RFC 3339 {{RFC3339}} `date-time` values with an explicit offset.
`date` is an RFC 3339 `full-date` and `time` is `HH:MM` (24-hour clock),
both in the merchant's local time zone. The params and result of each
intent are defined by the JSON Schemas of {{json-schemas}}; this section
states their meaning and errors.

| Intent | `merchant` | Purpose |
|---|---|---|
| DISCOVER | absent | Find merchants matching a query and preferences |
| AVAILABILITY | required | Up to 3 start times with a free table |
| BOOK | required | Create a booking |
| AMEND | required | Move a booking or change its party size |
| CANCEL | required | Cancel a booking as the guest |
| RECALL | absent | A guest's upcoming bookings and notices |
| PING | absent | Liveness check, keeps the session open |
{: title="MATP 0.1 intents"}

## DISCOVER

Params: `query` (string), and optionally `near` (string), `date`, and
`filters` with `tags` (array of strings) and `max_price`: one to four
EURO SIGN characters (U+20AC), from "€" (cheapest) to "€€€€".

Result: `merchants`, the live merchants matching the query and all
filters, each with `slug`, `name`, `city`, `tags`, `price_level` and
`website`. When filters exclude every merchant, `suggestions` lists which
filter to drop (`drop`) and how many merchants would then match
(`matches`). `ignored_filters` lists the tags the node does not know.

A caller MUST send the guest's preferences as `filters`. A node MUST NOT
store them beyond the request.

Errors: 400.

## AVAILABILITY

Params: `date`, `party_size` (integer, at least 1), and optionally `time`.

Result: `slots`, at most 3, each with `starts_at` and `table_label`. When
`time` is given, the node returns the start times closest to it (the
earlier one at equal distance); otherwise, the earliest ones. Start times
already past are never returned. The result is sorted by `starts_at`. An empty `slots` array means nothing is
free that day for that party.

Errors: 400, 404, 413, 503.

## BOOK

Params: `starts_at`, `party_size`, `guest_name` (non-empty string),
`idempotency_key` (non-empty string).

Result: `reservation`, with `id` (the merchant's reference, for example
"R-001"), `merchant`, `starts_at`, `party_size`, `table_label`, `status`
("confirmed"), `guest_name` and `via`.

`via` is the booking channel, as the merchant sees it. Version 0.1 defines
"claude", "merchant" and "website"; "website" is never set by MATP. A node
MUST record "merchant" when the caller's `agent.kind` is `merchant-agent`,
and "claude" when it is `user-agent`, whatever assistant the caller is. So
in this version, `via` is only accurate for callers behind the reference
bridge ({{security}}). A later version may add a value for other
assistants.

`starts_at` MUST be a start time the merchant offers; AVAILABILITY returns
such times. Errors: 400, 404, 409, 413, 503.

## AMEND

Params: `reservation_id`, `guest_name`, `idempotency_key`, and at least one
of `starts_at` and `party_size`.

The booking MUST be confirmed and MUST belong to `guest_name`; otherwise
the node answers 404, without saying which check failed. Result:
`reservation`, as amended.

Errors: 400, 404, 409, 413, 503.

## CANCEL

Params: `reservation_id`, `guest_name`. The same ownership rule as AMEND
applies. Result: `reservation`, with `status` "cancelled".

Errors: 400, 404, 503.

## RECALL

Params: `guest_name`.

Result: `reservations`, the guest's upcoming confirmed bookings at every
live merchant of the node, and `notices`, messages for the guest (for
example, that a merchant cancelled a booking). A node MUST return a notice
in every RECALL for the guest during 24 hours after the first RECALL that
returned it, and MUST NOT return it after that. An answer lost on its way
to the caller therefore loses no notice if the next RECALL comes within
those 24 hours, and a caller MAY send a RECALL again. A caller SHOULD NOT show the guest a notice it already showed them.

Errors: 400, 503.

## PING {#ping}

Params: `{}`. Result: `pong` (true) and `server_time`, the node's clock.

Errors: none defined.

# Status Codes {#status-codes}

| Status | `error.code` | Meaning | `fallback_url` |
|---|---|---|---|
| 200 | none | Success | no |
| 400 | `invalid_input` | Invalid params, unknown intent, missing or unexpected `merchant` | no |
| 401 | none | `hello` refused (in `reject` only) | no |
| 404 | `not_found` | Unknown merchant, merchant not yet live, unknown booking, or a booking that is not the guest's | no |
| 409 | `slot_unavailable` | The time is taken, blocked, in the past, or not one the merchant offers | no |
| 413 | `party_too_large` | The party is above the merchant's online limit, or no table fits it | yes |
| 426 | none | Unsupported major version (in `reject` only) | no |
| 429 | `too_many_requests` | Too many requests in flight in the session; retry after an earlier response | no |
| 503 | `agent_unavailable` | The merchant agent is down, failed, or timed out | yes |
{: title="MATP 0.1 status codes"}

The `status` values 400 and 413 also appear in `error` frames
({{error-frames}}), which carry no `error.code`; there, 413 means a frame
above the size limit.

A node MUST answer 503 to AVAILABILITY, BOOK, AMEND and CANCEL when the
merchant is marked as down, without consulting the merchant agent. A
caller that gets 413 or 503 MUST NOT retry the same request to the same
merchant in a loop; it SHOULD offer the guest the `fallback_url`.

A caller that receives a status it does not know MUST handle it as the
class of its first digit: 2 as success, 4 as a caller error, 5 as a node
error.

# Events {#events}

Frames of type `event`, sent by the node without a request, are reserved
for a later version. One intended use is to tell a guest's agent that a
merchant cancelled a booking; in this version, that reaches the guest as a
notice returned by RECALL. A 0.1 caller ignores them ({{error-frames}}).

# Versioning and Extensibility {#versioning}

A version is written `major.minor`:

~~~ abnf
version = 1*DIGIT "." 1*DIGIT
~~~

This document defines version 0.1. In `hello`, the caller sends the
highest version it speaks. If the node speaks the same major version, it
answers `welcome` with the highest minor version both speak, and both
sides use that version for the session. Otherwise it answers `reject` with
status 426.

Within a major version, a later minor version MAY add intents, optional
frame members, optional params, result members and status codes. It MUST
NOT change the meaning of what an earlier minor version defined. Because
unknown `params` members are invalid ({{requests}}), a caller MUST NOT send
params that the session's version does not define. A caller MUST ignore
result members it does not know.

# Security Considerations {#security}

Agent identity and tokens:
: The node attributes every request to the `agent.id` of its session. That
  attribution is only as good as the token check: a node MUST bind each
  token to one `agent.id` and refuse a `hello` whose token does not match.
  A node SHOULD compare tokens in constant time. Tokens MUST NOT be sent
  outside TLS except on the loopback interface, and SHOULD be long random
  strings that a node can revoke individually.

Transport:
: Without TLS, anyone on the path reads guest names, bookings and tokens,
  and can change them. {{transport}} therefore requires TLS 1.3 off the
  loopback interface. A node bound to the loopback interface is reachable
  by every local process: deployments on shared hosts SHOULD still require
  tokens.

Limits and denial of service:
: The frame size limit, the limit on requests in flight and the idle
  timeout bound what one session costs a node. A node SHOULD also limit
  sessions per peer and per agent. A node MUST stop reading a frame at the
  size limit instead of buffering it.

Booking channel:
: In version 0.1, a node records every `user-agent` booking with `via`
  "claude" ({{intents}}), and the merchant is told the booking came
  through Claude. With other assistants, that is wrong. A merchant SHOULD
  read `via` as "an assistant" until a later version names the calling
  agent in it.

Writes without confirmation:
: A misbehaving caller can book, amend or cancel without asking the guest.
  The node cannot detect it. Idempotency keys prevent duplicate bookings
  on retries, not unwanted ones.

Guest identity:
: In this version, a booking belongs to whoever gives its guest name and
  reference. Any authenticated caller that knows both can amend or cancel
  it, and RECALL returns the bookings of any name. This is acceptable only
  in an experiment with trusted callers. A deployment beyond that needs
  guest authentication, which a later version would define.

Error messages:
: `error.message` reaches people. A node MUST NOT put stack traces,
  internal names or data of other guests in it.

Fallback URLs:
: A caller passes `fallback_url` to a person. A caller SHOULD only offer
  `https` URLs, and a node SHOULD only send URLs on the merchant's own
  website.

# Privacy Considerations {#privacy}

Guest names:
: Guest names and party sizes are personal data. A node SHOULD NOT write
  guest names to logs at a level enabled in production. The reference
  implementation logs, for each request, the agent id, intent, merchant,
  status and latency, and nothing that names the guest.

Preferences:
: DISCOVER filters can reveal diet or religion (`vegan`, `halal`,
  `kosher`). A node MUST NOT store them beyond the request or link them to
  a guest.

Recall:
: As {{security}} says, RECALL returns the bookings of any name it is
  given, and for 24 hours the notices too: a notice names the merchant,
  the booking and its date. Callers SHOULD only send the name of the
  guest they act for.

Agent identity:
: `agent.id` identifies software, not a person. A caller SHOULD NOT put
  personal data in it.

# IANA Considerations {#iana}

This document has no IANA actions. The values below are used by this
experiment without registration.

Port:
: The reference implementation listens on TCP port 4710. That port is
  unassigned in the IANA Service Name and Transport Protocol Port Number
  Registry; this document does not request it. Registration would follow
  the procedures of {{RFC6335}}.

URI scheme:
: The `matp` scheme of {{transport}} is not registered. A later version
  would request provisional registration under {{RFC7595}} with this
  template:

  - Scheme name: matp
  - Status: Provisional
  - Applications/protocols that use this scheme name: the Merchant Agent
    Transfer Protocol (this document)
  - Contact: the authors of this document
  - Change controller: the authors of this document
  - References: this document

URN namespace:
: The `urn:matp:` identifiers used in examples are not a registered URN
  namespace ({{RFC8141}}).

--- back

# The MCP Bridge {#mcp-bridge}

An MCP server can let an assistant that only speaks MCP use a MATP node.
The reference bridge opens one session with `agent.id`
`urn:matp:agent:claude-gateway` and `agent.kind` `user-agent`, and maps its
tools to intents:

| MCP tool | MATP intent | Notes |
|---|---|---|
| `search_merchants` | DISCOVER | |
| `get_availability` | AVAILABILITY | the tool's `merchant` becomes the request's `merchant` |
| `create_reservation` | BOOK | idem |
| `update_reservation` | AMEND | idem |
| `cancel_reservation` | CANCEL | the tool returns `{"status": "cancelled"}` |
| `get_my_reservations` | RECALL | |
{: title="MCP tools and MATP intents"}

- The tool descriptions tell the assistant to confirm with the guest before
  `create_reservation`, `update_reservation` and `cancel_reservation`,
  which is how the bridge meets the confirmation requirement of
  {{requests}}.
- The bridge answers each tool call within 2 seconds, including the round
  trip to the node. A timeout, a lost session or a 503 becomes an
  `agent_unavailable` tool error with the merchant's `fallback_url`.
- A status and its `error` object become the tool error `{"error": code,
  "message": ..., "fallback_url": ...}`.
  `too_many_requests` is not a tool error code: the bridge stays under the
  limit, and would report a 429 as `agent_unavailable`.

# Example Session

The caller is the MCP bridge. Lines starting with "C:" go from the
caller to the node, and lines starting with "N:" from the node to the
caller.
Long frames are wrapped here for readability: on the wire, each is one
line.

~~~
C: {"type": "hello", "matp": "0.1",
    "agent": {"id": "urn:matp:agent:claude-gateway",
              "name": "Claude via the S\u00e9same gateway",
              "kind": "user-agent", "operator": "merchant-agents"},
    "token": "q7Lr..."}
N: {"type": "welcome", "matp": "0.1",
    "node": {"id": "urn:matp:node:merchant-agents",
             "name": "S\u00e9same"},
    "intents": ["DISCOVER", "AVAILABILITY", "BOOK", "AMEND", "CANCEL",
                "RECALL", "PING"],
    "session": "s-2f6c"}
C: {"type": "request", "id": "r-1", "intent": "DISCOVER",
    "params": {"query": "bistro", "filters": {"tags": ["vegetarian"],
               "max_price": "€€"}}}
N: {"type": "response", "id": "r-1", "status": 200,
    "result": {"merchants": [{"slug": "le-petit-zinc",
      "name": "Le Petit Zinc", "city": "Mulhouse",
      "tags": ["vegetarian", "terrace"], "price_level": "€€",
      "website": "https://le-petit-zinc.example/"}],
      "suggestions": [], "ignored_filters": []}}
C: {"type": "request", "id": "r-2", "intent": "AVAILABILITY",
    "merchant": "le-petit-zinc",
    "params": {"date": "2026-11-02", "time": "20:00", "party_size": 4}}
N: {"type": "response", "id": "r-2", "status": 200,
    "result": {"slots": [
      {"starts_at": "2026-11-02T19:30:00+01:00", "table_label": "T5"},
      {"starts_at": "2026-11-02T20:00:00+01:00", "table_label": "T5"},
      {"starts_at": "2026-11-02T20:30:00+01:00", "table_label": "T5"}]}}
~~~
{: title="Opening the session, discovery and availability"}

The caller then books, and checks a larger party while the booking is in
flight: the second answer arrives first.

~~~
C: {"type": "request", "id": "r-3", "intent": "BOOK",
    "merchant": "le-petit-zinc",
    "params": {"starts_at": "2026-11-02T20:00:00+01:00",
               "party_size": 4, "guest_name": "Ada Martin",
               "idempotency_key": "b1f0c2d4-3a9e"}}
C: {"type": "request", "id": "r-4", "intent": "AVAILABILITY",
    "merchant": "le-petit-zinc",
    "params": {"date": "2026-11-02", "party_size": 9}}
N: {"type": "response", "id": "r-4", "status": 413,
    "error": {"code": "party_too_large",
      "message":
        "Le Petit Zinc takes online bookings for up to 6 guests",
      "fallback_url": "https://le-petit-zinc.example/reservation.html"}}
N: {"type": "response", "id": "r-3", "status": 200,
    "result": {"reservation": {"id": "R-001",
      "merchant": "le-petit-zinc",
      "starts_at": "2026-11-02T20:00:00+01:00", "party_size": 4,
      "table_label": "T5", "status": "confirmed",
      "guest_name": "Ada Martin", "via": "claude"}}}
C: {"type": "request", "id": "r-5", "intent": "PING", "params": {}}
N: {"type": "response", "id": "r-5", "status": 200,
    "result": {"pong": true,
               "server_time": "2026-10-31T18:02:11+01:00"}}
~~~
{: title="Two requests in flight, and a ping"}

# JSON Schemas {#json-schemas}

These schemas use JSON Schema 2020-12 {{I-D.bhutton-json-schema}}. They are
generated from the reference implementation's contract models, so this
appendix and the implementation cannot drift {{MATP-SCHEMAS}}. In the
params of AVAILABILITY, BOOK, AMEND and CANCEL, the merchant is not a
member: it is the request's `merchant`.

## DISCOVER params

~~~ json
{::include schemas/discover.params.json}
~~~

## DISCOVER result

~~~ json
{::include schemas/discover.result.json}
~~~

## AVAILABILITY params

~~~ json
{::include schemas/availability.params.json}
~~~

## AVAILABILITY result

~~~ json
{::include schemas/availability.result.json}
~~~

## BOOK params

~~~ json
{::include schemas/book.params.json}
~~~

## BOOK, AMEND and CANCEL result

The three intents return the same `reservation` object.

~~~ json
{::include schemas/book.result.json}
~~~

## AMEND params

~~~ json
{::include schemas/amend.params.json}
~~~

## CANCEL params

~~~ json
{::include schemas/cancel.params.json}
~~~

## RECALL params

~~~ json
{::include schemas/recall.params.json}
~~~

## RECALL result

~~~ json
{::include schemas/recall.result.json}
~~~

## PING params and result

~~~ json
{::include schemas/ping.params.json}
~~~

~~~ json
{::include schemas/ping.result.json}
~~~

# Acknowledgments
{:numbered="false"}

The agent-only protocol idea comes from the AGTP Internet-Draft
{{I-D.hood-independent-agtp}}.
