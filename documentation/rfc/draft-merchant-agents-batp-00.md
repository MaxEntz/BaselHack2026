---
title: Broad Agent Transfer Protocol (BATP)
abbrev: BATP
docname: draft-merchant-agents-batp-00
category: exp
submissiontype: independent
ipr: trust200902
area: Applications
keyword:
  - agents
  - booking
  - MCP
date: 2026-11-01
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
  BATP-SCHEMAS:
    title: BATP JSON Schemas (generated from the reference implementation)
    target: https://github.com/MaxEntz/BaselHack2026/tree/main/documentation/rfc/schemas
    author:
      - org: Sésame (BaselHack 2026)
        orgascii: Sesame (BaselHack 2026)
    date: 2026-11-01

--- abstract

This document specifies the Broad Agent Transfer Protocol (BATP), an
application protocol reserved for software agents that book with merchants
on behalf of people or businesses. A BATP session runs over TCP, starts with
a handshake that names the calling agent, and carries newline-delimited JSON
frames. Each request states an intent: discover merchants, talk with a
merchant's agent in natural language, recall a guest's bookings, or ping.
A caller checks availability and books, changes or cancels a booking only
by talking with the merchant's agent, which proposes each write and carries
it out only once the caller confirms it. Responses carry numeric status
codes and, when the merchant's agent cannot serve the request, a fallback
URL to the merchant's website. BATP is designed to sit behind a Model
Context Protocol (MCP) server, which acts as a bridge for assistants that
only speak MCP.

--- middle

# Introduction

Software agents increasingly act for people: they search for a restaurant,
check a table and book it. When that traffic uses HTTP, a merchant cannot
tell it apart from a browser, so it cannot identify the calling agent,
route agent traffic separately, or apply rules meant for agents.

BATP gives that traffic its own channel. Every frame says which agent is
talking (established once per session), what it intends to do, and, when
it addresses one, to which merchant. The protocol is deliberately small: it
covers what a network of merchant agents needs to take bookings, and
nothing else.

This document defines version 0.3 of BATP. Version 0.2 added TALK, with
which a guest's agent talks with the merchant's agent in natural language
({{talk}}). Version 0.3 removes the four intents with which a caller
checked availability and booked, changed or cancelled directly: a caller
now does all of that through TALK. Only the merchant's agent, under the
control of the node's code, uses the merchant's booking operations, and
every write follows a proposal that the node built and a confirmation that
names it, whoever the caller is. The changes are listed in {{changes}}.

## Scope

This version covers the guest side of booking with restaurants: discovering
merchants, a conversation with the merchant's agent to check availability
and to book, change or cancel a booking, and recalling a guest's bookings.
Merchant-side operations (listing a day's bookings, blocking a
slot, changing a policy) are out of scope: they stay on the merchant's own
platform.

## Relation to MCP

Assistants such as Claude connect to remote tools through MCP {{MCP}}. An
MCP server can act as a bridge: each of its tools maps to one BATP intent
({{mcp-bridge}}). Any other agent can speak BATP directly.

## Relation to AGTP

The idea of an application protocol reserved for agent traffic, with agent
identity and intent-named methods, comes from the AGTP Internet-Draft
{{I-D.hood-independent-agtp}}. BATP is independent from it: it does not
implement AGTP, it uses a different transport framing and method set, and
it is limited to the booking use case.

# Conventions and Definitions

{::boilerplate bcp14-tagged}

This document uses the Augmented Backus-Naur Form (ABNF) notation of
{{RFC5234}}. JSON terminology ("object", "member", "string") is that of
{{RFC8259}}.

Node:
: A BATP server. It serves the merchant agents of one backend.

Merchant agent:
: One merchant's booking logic and data, served by a node and addressed by
  the merchant's slug, together with the conversational agent, built on a
  language model, that answers TALK for that merchant ({{talk}}). Since
  version 0.3, that conversational agent is the only way a caller reaches
  the merchant's booking operations.

Caller:
: An agent that opens a session with a node: a user's assistant, a
  merchant's agent, or a bridge acting for one of them.

Session:
: One transport connection between a caller and a node, from the caller's
  `hello` to the connection's close.

Frame:
: One JSON object followed by a line feed ({{framing}}).

Intent:
: What a request asks the node to do, for example `TALK`.

Slug:
: The stable identifier of a merchant on a node: 3 to 61 characters among
  lowercase ASCII letters, digits and "-".

Guest:
: The person a booking is for, identified in this version by the name the
  caller gives (see {{privacy}}).

Conversation:
: A sequence of TALK requests from one caller to one merchant agent, named
  by an opaque identifier that the node chooses ({{talk}}).

Proposal:
: A write (a booking, a change or a cancellation) that a merchant agent
  offers in a conversation, and carries out only when a later TALK confirms
  it ({{talk}}).

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
                                                          | BATP
  other agent ------------------- BATP --------------+    | TCP 4710
                                                     v    v
                                                   +-------------+
                                                   |  BATP node  |
                                                   +------+------+
                                                          |
                                         merchant agents (one per slug)
~~~
{: title="BATP deployment with an MCP bridge"}

~~~ ascii-art
  Caller                                        Node
    |  hello {agent, token, batp: "0.3"}         |
    |------------------------------------------->|
    |  welcome {batp: "0.3", node, intents,      |
    |           session}                         |
    |<-------------------------------------------|
    |  request {id: "r-1", intent: TALK}         |
    |------------------------------------------->|
    |  request {id: "r-2", intent: DISCOVER}     |
    |------------------------------------------->|
    |  response {id: "r-2", status: 200}         |
    |<-------------------------------------------|
    |  response {id: "r-1", status: 200}         |
    |<-------------------------------------------|
~~~
{: title="A session with two requests answered out of order"}

In this example, the TALK takes longer than the DISCOVER sent after it,
because the merchant's agent runs a model turn to answer it ({{deadlines}}).

# Transport {#transport}

BATP runs over TCP {{RFC9293}}. A node listens on a configured port. This
document uses port 4710 for its examples and its reference implementation;
that port is not registered for BATP ({{iana}}), so a caller MUST be able to
use another port.

On the loopback interface, a session MAY use plain TCP. On any other path,
the caller and the node MUST use TLS 1.3 {{RFC9846}} or a later version,
and the caller MUST authenticate the node's certificate against the host
name it connected to. TLS is set up before the first frame.

Either side MAY close the connection at any time. Closing the connection
ends the session. A request in flight when the session ends has no
response, and the caller MUST NOT assume it was or was not carried out
(see {{idempotency}} for retrying a TALK safely).

## Addressing

A merchant agent is named by a URI {{RFC3986}} of the form:

~~~ abnf
batp-URI = "batp://" host [ ":" port ] "/" slug
slug     = 3*61( %x61-7A / DIGIT / "-" )   ; a-z, 0-9, "-"
~~~

where `host` and `port` are those of {{RFC3986}}. When `port` is absent, it
is 4710. For example, `batp://node.example/le-petit-zinc` names the merchant
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
| `batp` | version string ({{versioning}}) | REQUIRED |
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
  uses identifiers of the form `urn:batp:agent:<name>`; `batp` is not a
  registered URN namespace {{RFC8141}}, so these identifiers are only
  meaningful between parties that agreed on them.
- `agent.kind` is `user-agent` when the caller acts for a guest, and
  `merchant-agent` when it acts for a business. The caller only declares
  it: the token proves `agent.id`, not `agent.kind` ({{talk-writes}}).
- `token` is the caller's credential on this node, issued out of band. A
  node MUST refuse a `hello` from a non-loopback peer that has no `token`.
  A node MAY accept a `hello` without `token` on the loopback interface.

The node MUST answer a `hello` with exactly one `welcome` or `reject`.

## welcome {#welcome}

| Member | Type | Requirement |
|---|---|---|
| `type` | `"welcome"` | REQUIRED |
| `batp` | the version of the session ({{versioning}}) | REQUIRED |
| `node` | object with `id` (string, REQUIRED) and `name` (string, OPTIONAL) | REQUIRED |
| `intents` | array of the intent names the node serves in this session | REQUIRED |
| `session` | string, the node's identifier for this session | REQUIRED |
{: title="Members of welcome"}

`intents` lists only intents that the session's version defines and that
the node's version has not removed: in a session of version 0.1, a node
does not list TALK, and a node of version 0.3 lists none of the intents
that version 0.3 removed, in a session of any version ({{versioning}}). A
caller MUST NOT send a request whose intent is not in `intents`. A node
that receives one MUST answer it with status 400 and code `invalid_input`,
without carrying it out; the session stays open.

## reject

| Member | Type | Requirement |
|---|---|---|
| `type` | `"reject"` | REQUIRED |
| `status` | 400, 401 or 426 | REQUIRED |
| `message` | string, for humans | REQUIRED |
| `batp` | the version the node speaks | OPTIONAL |
{: title="Members of reject"}

- 401: the agent is unknown to the node, the token is missing where it is
  required, or the token does not match the agent.
- 426: the node does not speak the major version in `hello.batp`
  ({{versioning}}). The node SHOULD include `batp`.
- 400: the first frame was not a valid `hello`.

After sending `reject`, the node MUST close the connection.

# Requests and Responses {#requests}

## request

| Member | Type | Requirement |
|---|---|---|
| `type` | `"request"` | REQUIRED |
| `id` | string, 1 to 64 characters | REQUIRED |
| `intent` | intent name ({{intents}}) | REQUIRED |
| `merchant` | slug | REQUIRED for TALK; absent otherwise |
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
| `fallback_url` | absolute URI | REQUIRED for `agent_unavailable`; absent otherwise |
{: title="Members of response.error"}

- A node MUST send exactly one response to each request it accepted, unless
  the session ends first.
- `result` MUST validate against the intent's result schema
  ({{json-schemas}}).
- `fallback_url` is the merchant's own booking page. A caller SHOULD give
  it to the guest, so that the guest can still book when the merchant
  agent cannot serve the request. The result of TALK has a `fallback_url`
  of its own, for a request that the agent answered but cannot serve
  ({{talk}}).
- `message` MUST NOT contain stack traces or internal identifiers
  ({{security}}).

## Concurrency {#concurrency}

A caller MAY send a request before earlier ones are answered. A node MAY
carry out the requests of one session concurrently, and MAY answer them in
any order. A caller MUST match responses to requests by `id` only.

A node MUST accept at least 32 requests in flight per session. A node MAY
answer a request beyond its limit with status 429 and code
`too_many_requests`, without carrying it out. The request itself is
valid: the caller MAY send it again once one of its earlier requests has
been answered.

## Deadlines {#deadlines}

A node SHOULD answer a TALK within 25 seconds of receiving it, and a
request of any other intent within 10 seconds. A TALK gets longer because
the merchant's agent answers it with a model turn and the tool calls that
turn makes ({{talk}}).

A node that has not carried out a request by the deadline it applies MUST
answer it with status 503 and code `agent_unavailable` ({{status-codes}}),
rather than leave it unanswered. A caller SHOULD wait for the answer to a
TALK at least as long as the node's deadline for TALK, so that the guest
gets the node's answer, or its `fallback_url`, rather than the caller's own
timeout. A caller that stops waiting for a request before its answer MUST
NOT assume that the request was or was not carried out.

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

A message sent again after a lost session must not book twice. In this
version, only TALK can carry out a write ({{talk-writes}}): DISCOVER,
RECALL and PING change nothing.

- TALK carries `params.idempotency_key`, chosen by the caller. A caller
  retrying a TALK MUST reuse the key of the first attempt, with the same
  params, and SHOULD use a fresh unpredictable key for every new message.
- The key is scoped to the merchant, the session's `agent.id` and the
  conversation; the key of a first message, which names no conversation
  yet, is scoped to the merchant and the `agent.id`. A node that receives a
  TALK with a key it already answered with status 200 in that scope, with
  the same `message`, `conversation` and `confirm`, MUST return the result
  it returned then, with status 200, without calling its model again and
  without carrying out any write. If one of them differs, the node MUST
  answer status 400 with code `invalid_input`, without carrying out
  anything. A TALK answered with another status does not use up its key.
- The node cannot tell a retry from another guest of the same caller who
  sends the same message with the same key. A caller that carries the
  messages of several guests under one `agent.id`, as a bridge does
  ({{mcp-bridge}}), MUST therefore choose keys that they cannot guess.
- A confirmed proposal has its own protection: the node carries it out at
  most once, whatever key the TALK that confirms it carries
  ({{talk-confirm}}).
- TALK is not read-only. A client library MUST NOT send a TALK again by
  itself after a lost session, as it may for DISCOVER, RECALL and PING; the
  caller decides, and retries with the same key.

## Confirmation before writes {#confirmation}

A booking, a change or a cancellation changes a guest's plans. In this
version, a caller can only obtain one through TALK, and the node's code
carries it out only on a `confirm` that names a proposal the node returned,
before the proposal expires ({{talk-confirm}}). This holds for every
caller, whatever it declared in `hello`.

What the node cannot check is that the guest agreed. A caller acting for a
guest MUST NOT send `confirm` before the guest explicitly accepted the
proposal's `summary`, after the caller showed it to them. A "yes" given
before the proposal was shown does not count.

# Intents {#intents}

Times are RFC 3339 {{RFC3339}} `date-time` values with an explicit offset.
`date` is an RFC 3339 `full-date`, in the merchant's local time zone. The
params and result of each intent are defined by the JSON Schemas of
{{json-schemas}}; this section states their meaning and errors.

| Intent | `merchant` | Purpose |
|---|---|---|
| DISCOVER | absent | Find merchants matching a query and preferences |
| TALK | required | Talk with the merchant's agent in natural language: ask, check availability, book, change or cancel (since 0.2) |
| RECALL | absent | A guest's upcoming bookings and notices |
| PING | absent | Liveness check, keeps the session open |
{: title="BATP 0.3 intents"}

Versions 0.1 and 0.2 also defined AVAILABILITY, BOOK, AMEND and CANCEL,
with which a caller checked availability and booked, changed or cancelled
without the merchant's agent. Version 0.3 removes them ({{versioning}}). A
node MUST answer a request for one of them as for an unknown intent, with
status 400 and code `invalid_input`, in a session of any version, without
carrying it out; the session stays open.

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

## TALK {#talk}

TALK is defined since version 0.2. A node MUST NOT offer or serve it in a
session of version 0.1 ({{versioning}}).

With TALK, a guest's agent talks with the merchant's agent in natural
language: it passes on what the guest wants, with the guest's preferences,
and the merchant's agent answers. To answer, the merchant's agent can check
availability, look up the guest's bookings at this merchant, and propose a
booking, a change or a cancellation. It carries out a proposal only when the
caller confirms it in a later TALK ({{talk-confirm}}). Since version 0.3,
TALK is the only intent with which a caller can book, change or cancel.

Params:

- `message` (REQUIRED): what the caller says to the merchant's agent. Once
  leading and trailing white space is removed, it MUST be 1 to 2000
  characters long, and it MUST NOT contain a control character (Unicode
  general category Cc) other than LF (U+000A).
- `idempotency_key` (REQUIRED): a string of 1 to 200 characters, chosen by
  the caller ({{idempotency}}).
- `conversation`: absent on the first message of a conversation. On every
  later message, the `conversation` that the node returned
  ({{conversations}}).
- `confirm`: the `id` of the conversation's `pending` proposal, sent to have
  it carried out. A caller MUST send it only after the guest's explicit
  confirmation ({{confirmation}}). A TALK with `confirm` MUST also carry
  `conversation`; a node answers one without it with status 400 and code
  `invalid_input`.

Conversation and proposal identifiers are opaque to the caller, and follow
this rule:

~~~ abnf
opaque-id = 1*64( ALPHA / DIGIT / "_" / "-" )
~~~

As for every intent, unknown params are invalid ({{requests}}).

Result:

- `conversation`: the conversation's identifier, an `opaque-id`: a new one
  on the first message, the one the caller sent on later messages.
- `reply`: the merchant agent's answer, at most 2000 characters. It is the
  only member written by the agent's language model. A caller SHOULD relay
  it to the guest. A caller MUST NOT take `reply` as proof that anything was
  booked, changed or cancelled: only `reservation` says so.

The other members are built by the node's code from the results of the
tools that the merchant's agent called, never from the model's text, so
that a caller can rely on them:

- `slots`: the start times the agent found free in its last check of
  availability while answering, sorted by `starts_at`, each with
  `starts_at` and `table_label` (the table that start time would get); an
  empty array when it found none or did not look. A slot is not held:
  another guest may take it before a proposal for it is confirmed.
- `pending`: the write the agent proposes, or null. It has `id` (an
  `opaque-id`), `action` ("book", "change" or "cancel"), `summary` (1 to 500
  characters, built by code from the proposal's own arguments, for the
  caller to show the guest) and `expires_at` (an RFC 3339 `date-time` with
  an offset). A conversation has at most one pending proposal: a new one
  replaces the previous one.
- `reservation`: the booking that a write carried out by this TALK created,
  changed or cancelled ({{talk-writes}}), with `id` (the merchant's
  reference, for example "R-001"), `merchant`, `starts_at`, `party_size`,
  `table_label`, `status` ("confirmed" or "cancelled"), `guest_name` and
  `via`; null when this TALK carried out no write.
- `fallback_url`: the merchant's own booking page, set when the agent
  cannot serve what the guest asked, for example after one of its tools
  refused it ({{talk-status}}); null otherwise.
- `tools_called`: the names of the tools the agent called while answering,
  in the order of the calls, without their arguments or results. On a TALK
  whose `confirm` names the conversation's pending proposal, the first name
  is that of the write the node's code ran or tried for it
  ({{talk-writes}}), although the model did not call it. Each name is 1 to
  64 characters: a lowercase ASCII letter, then lowercase ASCII letters,
  digits and "_".

`conversation` and `reply` are always present. A caller handles an absent
`pending`, `reservation` or `fallback_url` as null, and an absent `slots` or
`tools_called` as an empty array.

### Proposals and confirmation {#talk-confirm}

The merchant's agent has no tool that writes: a booking, a change or a
cancellation is first a proposal, returned in `pending`.

- A node MUST NOT carry out a write proposed in a conversation until a
  later TALK of the same conversation names the proposal's `id` in
  `confirm`, before its `expires_at`. The node's code MUST enforce this,
  whatever the model outputs and whatever the message says: a message that
  says "yes" without `confirm` carries out nothing.
- On a TALK whose `confirm` names the conversation's pending proposal before
  it expires, the node carries out that proposal with the arguments stored
  with it, before it calls the model, so that neither the message nor the
  model's output can change what is written.
- A node MUST carry out a proposal at most once, even when the TALK that
  confirms it is sent again under another key.
- A `confirm` that names no pending proposal of the conversation (unknown,
  replaced or expired) carries out no write. The node answers it with
  status 200, and `reply` says that nothing was done.
- If the write fails when it is carried out, for example because the time
  was taken in the meantime, nothing is written and the proposal is gone.
  The node answers with status 200; the agent MAY offer other times in a
  new proposal, which needs a new confirmation.
- When this TALK carried out a write, the node MUST set `reservation`, even
  if its model fails afterwards.

### Writes {#talk-writes}

A confirmed proposal runs one of the merchant's booking operations, with
the arguments stored with the proposal. Its `action` says which:

- "book" creates a booking for the proposal's start time, party size and
  guest name. A node MUST NOT create it unless the start time is one the
  merchant offers and a table that fits the party is free at that time
  when the write runs; the node chooses the table. The write is named
  "create_reservation" in `tools_called`.
- "change" moves a booking, changes its party size, or both, with the same
  conditions for the new start time and party size. It is named
  "update_reservation".
- "cancel" cancels a booking, as the guest. It is named
  "cancel_reservation".

A change or a cancellation applies only to an upcoming confirmed booking at
this merchant, under the guest name given in the proposal, and only before
the booking starts. The merchant's agent cannot propose one otherwise, and
the node does not say which of these conditions failed.

`via` is the booking channel, as the merchant sees it. Versions 0.1 to 0.3
define "claude", "merchant" and "website"; "website" is never set by BATP.
A node MUST record a booking that a TALK creates with `via` "claude",
whatever `agent.kind` the session declared: the caller declares its kind,
and its token does not prove it, so the node does not trust it. A change or
a cancellation keeps the booking's `via`. In this version, `via` therefore
names the channel rather than the calling assistant ({{security}}); a later
version may add a value for other assistants.

### Conversations {#conversations}

- A node MUST choose each `conversation` at random, with at least 128 bits
  of entropy, and SHOULD choose each `pending.id` in the same way.
- A node binds a conversation, when it creates it, to the merchant and to
  the session's `agent.id`. It MUST answer status 404 to a TALK whose
  `conversation` is unknown, has expired, or was created for another
  merchant or another `agent.id`, without saying which.
- A conversation is not tied to a BATP session: a caller MAY continue it in
  a later session under the same `agent.id`, for example after a lost
  session. A bridge carries the conversations of all its users on one
  session, under one `agent.id` ({{mcp-bridge}}), so only the unguessable
  identifier separates them.
- A node MAY expire a conversation, for example after a period without
  messages or once it has had a number of messages, or lose it when it
  restarts. A caller that gets status 404 for a conversation MAY start a
  new one, without `conversation`.

### Status {#talk-status}

Status 200 means that the merchant's agent answered, whatever the answer,
including a refusal such as "we are full". When one of the agent's tools
refuses, for example because the time is taken or the party is above the
merchant's online limit, the refusal goes to the model, which words it in
`reply`. When the agent cannot serve the request at all, as for a party
above that limit, the node's code also sets `fallback_url`. The TALK still
gets status 200: since version 0.3, no request is answered with status 409
or 413 ({{status-codes}}). Any other status means that the agent could not
answer:

- 400 `invalid_input`: invalid params, `confirm` without `conversation`, a
  key already used with other params ({{idempotency}}), or a TALK in a
  session of version 0.1.
- 404 `not_found`: the merchant is unknown or not yet live, or the
  conversation is unknown, has expired, or belongs to another merchant or
  another `agent.id`.
- 429 `too_many_requests`: too many requests in flight in the session
  ({{concurrency}}). A node MAY also answer 429, without calling its model,
  to a TALK beyond the limits it sets on model use, such as the model turns
  running at once or the TALK requests a merchant receives per minute. The
  caller MAY send it again later, with the same key.
- 503 `agent_unavailable`, with `fallback_url`: the merchant is marked as
  down, the agent failed, or it did not answer by its deadline
  ({{deadlines}}).

Errors: 400, 404, 429, 503.

## RECALL

Params: `guest_name`.

Result: `reservations`, the guest's upcoming confirmed bookings at every
live merchant of the node, each as `reservation` in the result of TALK
({{talk}}), and `notices`, messages for the guest (for
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
| 400 | `invalid_input` | Invalid params, unknown intent (including one that version 0.3 removed) or one the session's version does not define, missing or unexpected `merchant` | no |
| 401 | none | `hello` refused (in `reject` only) | no |
| 404 | `not_found` | Unknown merchant, merchant not yet live, or an unknown or expired conversation | no |
| 413 | none | Frame above the size limit (in `error` frames only) | no |
| 426 | none | Unsupported major version (in `reject` only) | no |
| 429 | `too_many_requests` | Too many requests in flight in the session, or a TALK beyond the node's limits; retry later | no |
| 503 | `agent_unavailable` | The merchant agent is down, failed, or did not answer by its deadline | yes |
{: title="BATP 0.3 status codes"}

The status 400 also appears in `error` frames ({{error-frames}}), and 413
only there; `error` frames carry no `error.code`.

Versions 0.1 and 0.2 also answered status 409 with code `slot_unavailable`
(the time is taken, blocked, in the past, or not offered) and status 413 with code
`party_too_large` (the party is above the merchant's online limit, or no
table fits it) to the intents that version 0.3 removed. A node of version
0.3 answers neither to any request: inside TALK, the refusals they stood
for are part of an answer with status 200 ({{talk-status}}). A later
version MUST NOT give these two codes another meaning.

A node MUST answer 503 to a TALK addressed to a merchant marked as down,
without calling its model. A caller that gets 503 MUST NOT retry the same
request to the same merchant in a loop; it SHOULD offer the guest the
`fallback_url`.

A caller that receives a status it does not know MUST handle it as the
class of its first digit: 2 as success, 4 as a caller error, 5 as a node
error.

# Events {#events}

Frames of type `event`, sent by the node without a request, are reserved
for a later minor version: version 0.3 does not define them. One intended
use is to tell a guest's agent that a merchant cancelled a booking; in this
version, that reaches the guest as a notice returned by RECALL. A caller of
versions 0.1 to 0.3 ignores them ({{error-frames}}).

# Versioning and Extensibility {#versioning}

A version is written `major.minor`:

~~~ abnf
version = 1*DIGIT "." 1*DIGIT
~~~

This document defines version 0.3. Version 0.2 added TALK to version 0.1.
Version 0.3 removes AVAILABILITY, BOOK, AMEND and CANCEL ({{intents}}), and
records every booking made through TALK with `via` "claude", where version
0.2 recorded "merchant" for a caller that declared itself a
`merchant-agent` ({{talk-writes}}). Its other changes state more precisely
what version 0.2 defined for TALK ({{changes}}).

In `hello`, the caller sends the highest version it speaks. If the node
speaks the same major version, it answers `welcome` with the highest minor
version both speak, which is the lower of the caller's minor version and
its own, and both sides use that version for the session. Otherwise it
answers `reject` with status 426. A node speaks every earlier minor version
of its major version: a session of an earlier version gets the intents
that version defined, less those that the node's version removed. For
example, a node of version 0.3 answers a `hello` with `"batp": "0.1"` with
a `welcome` with `"batp": "0.1"`, and the session is a session of version
0.1, whose intents are DISCOVER, RECALL and PING.

A node MUST NOT list in `welcome.intents` an intent that the session's
version does not define or that the node's version removed, and MUST
answer a request for one with status 400 and code `invalid_input`
({{welcome}}). In a session of version 0.1, a node of version 0.3
therefore offers and serves neither TALK nor the removed intents: a caller
of version 0.1 can still discover merchants and recall bookings, but can no
longer book. In a session of version 0.2 or 0.3, it offers DISCOVER, TALK,
RECALL and PING.

Within a major version, a later minor version MAY add intents, optional
frame members, optional params, result members and status codes: adding an
intent, as version 0.2 did with TALK, is a minor change. It MUST NOT
change the meaning of what an earlier minor version defined. While the
major version is 0, a minor version MAY also remove intents, as version 0.3
does, and correct a rule of an earlier minor version, as version 0.3 does
for `via`. Removing an intent is not backward compatible: a caller that
relied on it can no longer use it. It is accepted only because version 0
is experimental; from version 1.0, it would need a new major version. A
node answers a removed intent as an unknown one, with status 400 and code
`invalid_input`. Because unknown `params` members are invalid
({{requests}}), a caller MUST NOT send params that the session's version
does not define. A caller MUST ignore result members it does not know.

# Security Considerations {#security}

Agent identity and tokens:
: The node attributes every request to the `agent.id` of its session. That
  attribution is only as good as the token check: a node MUST bind each
  token to one `agent.id` and refuse a `hello` whose token does not match.
  A node SHOULD compare tokens in constant time. Tokens MUST NOT be sent
  outside TLS except on the loopback interface, and SHOULD be long random
  strings that a node can revoke individually. A token proves `agent.id`
  only: `agent.kind` is the caller's claim, and a node does not grant or
  record anything on the strength of it ({{talk-writes}}).

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
  size limit instead of buffering it. A TALK costs a model turn, which is
  far more expensive than any other request: a node SHOULD bound the model
  turns it runs at once and the TALK requests each merchant receives per
  unit of time, and answer 429 beyond them without calling its model
  ({{talk-status}}).

Booking channel:
: In this version, a node records every booking made through TALK with
  `via` "claude" ({{talk-writes}}), whatever `agent.kind` the caller
  declared, and the merchant is told the booking came through Claude. With
  other assistants, that is wrong. A merchant SHOULD read `via` as "an
  assistant" until a later version names the calling agent in it.

Writes without confirmation:
: Since version 0.3, no caller can book, change or cancel except through
  TALK, and the node's code carries out a write only on a `confirm` that
  names a proposal it returned, before the proposal expires
  ({{talk-confirm}}). This holds for every caller, whatever its
  `agent.kind`, and no longer depends on what a bridge tells its
  assistant. A misbehaving caller can still send `confirm` without asking
  the guest: the node cannot tell whether the guest agreed. Idempotency
  keys and the rule that a proposal runs at most once prevent duplicate
  writes on retries, not unwanted ones.

A language model reading untrusted text:
: The merchant's agent that answers TALK is a language model, and
  `message` is text it cannot trust: the guest's agent may relay anything
  that a person or a web page told it, including instructions written to
  make the model book, cancel or reveal data (prompt injection). A node
  therefore relies on its code, not on the model's instructions, for
  everything below.

Confirmation of writes in code:
: A write proposed through TALK runs only on a later `confirm` that names
  its proposal before it expires, with the arguments stored with the
  proposal ({{talk-confirm}}). This check MUST be made by the node's code,
  never left to the model: the model has no tool that writes, so no output
  of the model alone can create, change or cancel a booking.

Tools and merchant binding:
: The merchant's agent MUST only be given the guest-side operations of its
  own merchant (checking availability, finding the guest's bookings at
  that merchant, and proposing a booking, a change or a cancellation),
  never merchant-side operations such as listing a day's bookings or
  blocking a slot. The
  merchant MUST be bound by the node's code, from the request's
  `merchant`, and MUST NOT be an argument of any tool the model fills in,
  so that one merchant's agent cannot read or change another merchant's
  data, whatever the model is told.

Website content:
: The text crawled from the merchant's website MUST NOT enter the agent's
  prompt as such. Only data validated against a schema and reviewed by
  the merchant before publishing may, and the agent's prompt marks it as
  data, not instructions: it was still taken from a website, which anyone
  who can edit that website controls.

Conversation identifiers:
: A conversation identifier is a capability: with it, and the token of the
  `agent.id` it is bound to, a caller can continue the conversation and
  confirm its proposal. A node MUST make it unguessable and bind it to the
  merchant and the `agent.id` ({{conversations}}). A node and a bridge
  MUST NOT log conversation identifiers together with guest names at a
  level enabled in production, and SHOULD log a digest of the identifier
  rather than the identifier itself.

Model output:
: `reply` is written by a model and can be wrong. A caller MUST NOT tell
  the guest that a booking was made, changed or cancelled unless
  `reservation` says so. Before asking the guest to confirm, a caller
  SHOULD show them `pending.summary`, which the node's code builds from the
  proposal itself.

Guest identity:
: In this version, a booking belongs to whoever gives its guest name and
  reference. Any authenticated caller that knows both can have it changed
  or cancelled through TALK, and RECALL returns the bookings of any name. This is acceptable only
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

Conversations:
: TALK messages and replies are free text, and may carry guest names,
  preferences such as diet or religion, and anything else the guest said.
  The same rule as for guest names applies: a node and a bridge SHOULD NOT
  write `message` or `reply` to logs at a level enabled in production.
  Unlike DISCOVER filters, which a node does not keep, a conversation keeps
  what was said in it for as long as the node keeps the conversation: a
  node SHOULD keep a conversation no longer than it accepts messages in it,
  and SHOULD NOT link it to a guest beyond it. A node that shows its
  merchant the requests it received shows the merchant what the guest's
  agent said.

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
: The `batp` scheme of {{transport}} is not registered. A later version
  would request provisional registration under {{RFC7595}} with this
  template:

  - Scheme name: batp
  - Status: Provisional
  - Applications/protocols that use this scheme name: the Broad Agent
    Transfer Protocol (this document)
  - Contact: the authors of this document
  - Change controller: the authors of this document
  - References: this document

URN namespace:
: The `urn:batp:` identifiers used in examples are not a registered URN
  namespace ({{RFC8141}}).

--- back

# The MCP Bridge {#mcp-bridge}

An MCP server can let an assistant that only speaks MCP use a BATP node.
The reference bridge opens one session with `agent.id`
`urn:batp:agent:claude-gateway` and `agent.kind` `user-agent`, and maps its
tools to intents:

| MCP tool | BATP intent | Notes |
|---|---|---|
| `search_merchants` | DISCOVER | |
| `talk_to_merchant` | TALK | the tool's `merchant` becomes the request's `merchant` |
| `get_my_reservations` | RECALL | |
{: title="MCP tools and BATP intents"}

The tool `talk_to_merchant(merchant, message, idempotency_key,
conversation?, confirm?)` passes its arguments other than `merchant` as the
params of TALK, and returns the result of TALK. Since version 0.3, it is
the only tool with which the assistant can check availability or book,
change or cancel. The reference bridge also serves one MCP endpoint per
merchant, whose only tool is `talk_to_merchant` without `merchant`: the
request's `merchant` is the endpoint's.

- The description of `talk_to_merchant` tells the assistant to relay
  `reply`, to show `pending.summary` and ask the guest when `pending` is
  set, to send `confirm` only after the guest's explicit yes to that
  summary, never in the same reply as the call that returned `pending`,
  and to give the guest any `fallback_url`. That is how the bridge meets
  the caller's part of {{confirmation}}; the node's code enforces the rest.
- The tool takes a random UUID as `idempotency_key`, one per message: the
  bridge carries all its users under one `agent.id`, so their keys must not
  be guessable ({{idempotency}}).
- The bridge answers each tool call within 2 seconds, including the round
  trip to the node, except `talk_to_merchant`, which it gives 30 seconds:
  longer than the node's deadline for TALK ({{deadlines}}), so that the
  node's 503 and its `fallback_url` arrive before the bridge gives up. A
  timeout, a lost session or a 503 becomes an `agent_unavailable` tool
  error with the merchant's `fallback_url`. When the call carried
  `confirm`, the error adds that the write may have gone through, and that
  the assistant should check with `get_my_reservations` before sending the
  message again with the same key.
- The bridge carries the conversations of all its users on its one
  session, under one `agent.id`: only the conversation identifier, which
  the node makes unguessable ({{conversations}}), keeps one user from
  continuing another's conversation. A 404 for an unknown or expired
  conversation is a `not_found` tool error, not an outage: the assistant
  starts a new conversation.
- A status and its `error` object become the tool error `{"error": code,
  "message": ..., "fallback_url": ...}`.
  `too_many_requests` is not a tool error code: the bridge stays under the
  limit on requests in flight, and reports any 429, including one for a
  TALK beyond the node's limits on model use, as `agent_unavailable`.

# Example Session

The caller is the MCP bridge. Lines starting with "C:" go from the
caller to the node, and lines starting with "N:" from the node to the
caller.
Long frames are wrapped here for readability: on the wire, each is one
line.

~~~
C: {"type": "hello", "batp": "0.3",
    "agent": {"id": "urn:batp:agent:claude-gateway",
              "name": "Claude via the Sésame gateway",
              "kind": "user-agent", "operator": "merchant-agents"},
    "token": "q7Lr..."}
N: {"type": "welcome", "batp": "0.3",
    "node": {"id": "urn:batp:node:merchant-agents",
             "name": "Sésame"},
    "intents": ["DISCOVER", "TALK", "RECALL", "PING"],
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
~~~
{: title="Opening the session and discovery"}

The caller then talks with the merchant's agent. Its first message opens a
conversation. While the TALK waits for the agent's model turn, the caller
sends a ping, which is answered first.

~~~
C: {"type": "request", "id": "r-2", "intent": "TALK",
    "merchant": "le-petit-zinc",
    "params": {"message":
      "Table for 4 on Monday around 20:00? Any vegetarian dishes?",
      "idempotency_key": "0b6f3a9c-1e2d-4c5b-9a8f-7e6d5c4b3a21"}}
C: {"type": "request", "id": "r-3", "intent": "PING", "params": {}}
N: {"type": "response", "id": "r-3", "status": 200,
    "result": {"pong": true,
               "server_time": "2026-10-31T18:02:11+01:00"}}
N: {"type": "response", "id": "r-2", "status": 200,
    "result": {"conversation": "c-Zx3kQ9mLr2VtB7nHc4WpYa",
      "reply":
        "Yes, two vegetarian mains. I can do 19:30, 20:00 or 20:30.",
      "slots": [
      {"starts_at": "2026-11-02T19:30:00+01:00", "table_label": "T5"},
      {"starts_at": "2026-11-02T20:00:00+01:00", "table_label": "T5"},
      {"starts_at": "2026-11-02T20:30:00+01:00", "table_label": "T5"}],
      "pending": null, "reservation": null, "fallback_url": null,
      "tools_called": ["get_availability"]}}
~~~
{: title="A first message, with a ping in flight"}

The guest picks a time and gives a name, which the caller passes on. The
merchant's agent turns the request into a proposal.

~~~
C: {"type": "request", "id": "r-4", "intent": "TALK",
    "merchant": "le-petit-zinc",
    "params": {"message": "20:00 please, for Ada Martin.",
      "conversation": "c-Zx3kQ9mLr2VtB7nHc4WpYa",
      "idempotency_key": "5d2e8f41-7a3b-4e6c-8d9f-0a1b2c3d4e5f"}}
N: {"type": "response", "id": "r-4", "status": 200,
    "result": {"conversation": "c-Zx3kQ9mLr2VtB7nHc4WpYa",
      "reply": "Shall I book a table for 4 at 20:00 for Ada Martin?",
      "slots": [],
      "pending": {"id": "p-Kq81sVd0LmZr6TfXe2NbA", "action": "book",
        "summary":
          "Table for 4 on Monday 2 November 2026 at 20:00, Ada Martin",
        "expires_at": "2026-10-31T18:14:05+01:00"},
      "reservation": null, "fallback_url": null,
      "tools_called": ["propose_booking"]}}
~~~
{: title="Up to a proposal"}

The caller shows the guest the proposal's `summary`. Once the guest has
said yes, the caller confirms the proposal, and the node books the table
with the proposal's own arguments, before its model words the answer.
`tools_called` names that write, although the model called no tool.

~~~
C: {"type": "request", "id": "r-5", "intent": "TALK",
    "merchant": "le-petit-zinc",
    "params": {"message": "Yes, please book it.",
      "conversation": "c-Zx3kQ9mLr2VtB7nHc4WpYa",
      "confirm": "p-Kq81sVd0LmZr6TfXe2NbA",
      "idempotency_key": "9c4b7e2a-3f1d-4a8b-b6e5-2d7c1f0e9a83"}}
N: {"type": "response", "id": "r-5", "status": 200,
    "result": {"conversation": "c-Zx3kQ9mLr2VtB7nHc4WpYa",
      "reply": "Booked for Monday at 20:00. Your reference is R-001.",
      "slots": [], "pending": null,
      "reservation": {"id": "R-001", "merchant": "le-petit-zinc",
        "starts_at": "2026-11-02T20:00:00+01:00", "party_size": 4,
        "table_label": "T5", "status": "confirmed",
        "guest_name": "Ada Martin", "via": "claude"},
      "fallback_url": null, "tools_called": ["create_reservation"]}}
~~~
{: title="Confirming the proposal"}

The guest then asks for a party above the merchant's online limit. The
agent's availability check refuses it: the answer still has status 200,
with the merchant's own booking page in `fallback_url`.

~~~
C: {"type": "request", "id": "r-6", "intent": "TALK",
    "merchant": "le-petit-zinc",
    "params": {"message": "And a table for 9 on Friday at 20:00?",
      "conversation": "c-Zx3kQ9mLr2VtB7nHc4WpYa",
      "idempotency_key": "e1a7c3f5-8b2d-4f6a-9c0e-4b3a2d1c0f97"}}
N: {"type": "response", "id": "r-6", "status": 200,
    "result": {"conversation": "c-Zx3kQ9mLr2VtB7nHc4WpYa",
      "reply":
        "We take online bookings for up to 6. Please book on our site.",
      "slots": [], "pending": null, "reservation": null,
      "fallback_url":
        "https://le-petit-zinc.example/reservation.html",
      "tools_called": ["get_availability"]}}
~~~
{: title="A refusal inside the conversation"}

# JSON Schemas {#json-schemas}

These schemas use JSON Schema 2020-12 {{I-D.bhutton-json-schema}}. They are
generated from the reference implementation's contract models, so this
appendix and the implementation cannot drift {{BATP-SCHEMAS}}. In the
params of TALK, the merchant is not a member: it is the request's
`merchant`.

## DISCOVER params

~~~ json
{::include schemas/discover.params.json}
~~~

## DISCOVER result

~~~ json
{::include schemas/discover.result.json}
~~~

## TALK params

The schema does not express two rules of {{talk}}: `message` is measured
once leading and trailing white space is removed, and `confirm` requires
`conversation`.

~~~ json
{::include schemas/talk.params.json}
~~~

## TALK result

~~~ json
{::include schemas/talk.result.json}
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

# Changes from Earlier Versions {#changes}

## Version 0.3

- Removed the intents AVAILABILITY, BOOK, AMEND and CANCEL: a caller checks
  availability and books, changes or cancels only through TALK. A node
  answers them as unknown intents, with status 400 and code
  `invalid_input`, in a session of any version ({{intents}}).
- TALK is the only intent that takes `merchant` ({{requests}}).
- Stated what a confirmed proposal writes, and that a booking made through
  TALK is recorded with `via` "claude" whatever `agent.kind` the caller
  declares, since no token proves it; version 0.2 required "merchant" for a
  `merchant-agent` ({{talk-writes}}).
- `tools_called` names the write that the node's code ran on a `confirm`
  ({{talk}}).
- No request is answered with status 409 or 413 any more; 413 remains for
  frames above the size limit, and a later version cannot give
  `slot_unavailable` or `party_too_large` another meaning
  ({{status-codes}}).
- Idempotency keys are those of TALK only, scoped to the conversation; a
  key sent again with other params is invalid, and a caller that carries
  several guests chooses keys they cannot guess ({{idempotency}}).
- Confirmation of writes is enforced by the node's code for every caller
  ({{confirmation}}).
- While the major version is 0, a minor version may remove intents
  ({{versioning}}).
- The MCP bridge maps three tools, and books only through
  `talk_to_merchant` ({{mcp-bridge}}).

## Version 0.2

- Added the TALK intent ({{talk}}), with its conversations and its
  proposals, which the node carries out only on `confirm`; TALK is
  addressed, so `merchant` is required for it.
- Stated how the session's version is chosen, and that a session of
  version 0.1 is neither offered nor served TALK ({{versioning}}).
- Added deadlines for answering requests: 25 seconds for TALK, 10 seconds
  for the other intents ({{deadlines}}).
- Added TALK to the idempotency rules, and said that a client library does
  not send a TALK again by itself ({{idempotency}}).
- Moved the reservation of `event` frames to a later minor version
  ({{events}}).
- Added the bridge's `talk_to_merchant` tool ({{mcp-bridge}}).
- Added the security and privacy considerations of a merchant agent built
  on a language model ({{security}}, {{privacy}}).

# Acknowledgments
{:numbered="false"}

The agent-only protocol idea comes from the AGTP Internet-Draft
{{I-D.hood-independent-agtp}}.
