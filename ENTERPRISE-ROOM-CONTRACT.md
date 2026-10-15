# Enterprise room interfaces and minimum permission slice

Current deployed contract: Hollywood 0.2.0, HTTP API v2, owner bearer token.
Generated UI and Slack adapters should use these supported interfaces through a
trusted server. Never distribute the environment-owner bearer token to generated
UI, Slack users or customer browsers. This token can access every team, including
private conversations; team-scoped URLs are not per-user authorization.

## Supported interfaces

Use team-scoped `/hollywood/v2/teams/{team}/` routes for room discovery/detail,
messages/history/events, message detail, command receipt lookup and attention
answers. `TEAM_API.md` specifies pagination and request shapes;
`ATTENTION-CLIENT-CONTRACT.md` pins question acceptance and receipt semantics.

Stable authority/room/actor IDs identify the environment and communication
participants. Message targets are registered agent slugs, not actor UUIDs.
Explicit room send is `{commandId,task,targets,visibility:'room',body}`; private
send uses one target and `visibility:'direct'`. A reply carries `replyTo` and
preserves the originating context. Accepted commands have durable digest conflict
checks; receipt lookup returns the event and delivery rows. Rendered buttons must
represent an explicitly allowed operation and retain original context/command ID.

Membership and rendering grant neither execution nor filesystem authority.
Owner room tools preserve tool authority; peer summaries do not become human
requests. Native model/thread settings remain runtime-owned. Customer-selected
model labels or browser claims cannot override runtime permissions.

## Staged, not deployed

The working candidate adds explicit room owners, generic dynamic-tool receipts,
work-order assignment records and completion/blocker delivery to Global. These
changes have focused fixture evidence, not an enterprise multi-user qualification.
Work-order dependencies are held without execution; native goal acceptance and
release are not implemented. Room owner and agent identity are not human-principal
permissions. The candidate must not be advertised as providing tenant isolation,
Slack production scale, on-prem qualification or a complete autonomy backend.

## Minimum missing implementation for a customer-facing demo

Agree one explicitly scoped demo: two authenticated human principals, one team,
two rooms, one private conversation, and a small action set. Add a trusted
principal-to-grant boundary in front of the existing API rather than a parallel
incompatible UI backend. Grants must identify environment authority, principal,
team, room/task, allowed operation and private-conversation identity where needed.
Default deny; authenticated principal identity must come from the trusted adapter,
not generated HTML, room membership, Slack display name or request JSON.

The boundary must authorize before resolving event IDs, command IDs, replies,
question answers or streaming history. Filter private data before returning
collections. Retain the authenticated principal in action audit/receipt metadata;
namespace caller command IDs so two principals cannot collide or query each
other's receipts. A grant revision must be checked again on a new action; cached
UI or replayed Slack interaction cannot restore revoked permission. A read-only
grant never admits an agent turn. Token provisioning and grant administration
remain an explicit owner operation.

## Minimum verification and release gate

### Isolated demo adapter boundary (proposal, not a deployed API)

For the engineering demo at
`/aosp/development/ozzz-enterprise-readiness/paid-pilot-20261011-r01/demos/enterprise-room-ui`,
fictional Ari/Camille views and context/coach/delivery agents remain local fixtures.
The browser may submit an immutable `operationId`, selected `roomId`, action
(`room.read`, `room.post`, `suggestion.request`, `receipt.read`, `operation.cancel`)
and action input. It must not supply authoritative tenant, principal, role, grants,
native thread or execution identity. Treat any such client fields as untrusted.

A future server adapter obtains `tenantId` and `principalId` from a verified
server session, resolves room mapping and current membership/grant revision, then
checks the exact action and intended audience before any upstream call. Slack
would require its own verified workspace/user identity mapping; no such adapter
is implemented. Rendered controls are affordances, not grants. Local fixture
identity selection must remain visibly simulated and cannot authorize the server.

Persist an operation record scoped to `(authorityId, tenantId, principalId,
operationId)` with canonical action/input digest, resolved team/task/agent,
audience/grant revision and downstream command ID. Reuse returns the original
receipt only after checking current read permission; changed action/input
conflicts. Persist intent before submission. Store owner bearer credentials only
on the trusted server. An upstream owner bearer must never stand in for the
individual principal in adapter audits or authorization.

Receipts need distinct facts: `accepted` (adapter persisted request),
`submitted` (upstream command accepted), `delivered` (native delivery confirmed),
and `completed` (explicit operation result under its acceptance criteria).
`unknown` retains original command identity and prevents blind resubmission.
Return only audience-authorized receipt/result data; a known operation ID grants
no read access. Cursors bind authority/tenant/principal/room/audience and grant
revision; revalidate before every page or streamed event. Do not reuse the
environment-wide event cursor as a private-data access token.

`operation.cancel` must distinguish request from effect. A local unsubmitted
fixture operation can become cancelled; an already submitted native operation
requires its own supported cancellation receipt. Hollywood control interrupt
targets a task/agent turn and does not prove operation-specific effect rollback.
Until such a contract exists, expose cancellation as unsupported or requested/
unknown, never completed or undone. Requesting a bounded suggestion likewise
requires explicit runtime admission/grant and budget; the demo currently simulates
it. These proposed operation states are adapter facts, not new API2 server fields.

Exercise two users with disjoint room/private grants: allowed reads/actions,
foreign room/event/receipt access, forged principal/actor fields, revoked grants,
cross-room replyTo, private history and stream leakage. Test simultaneous same-ID
retries and changed payload conflicts, duplicate Slack delivery/action callbacks,
accepted-but-response-lost recovery and unknown native effects. Preserve original
environment/team/task/question/receipt identities throughout.

Qualify one visible demo flow: permitted user asks agent A, agent A delegates
bounded work to registered agent B, result notifies the accountable coordinator,
human question returns to the owning context, and unauthorized user sees neither
private content nor an executable action. Label acceptance, delivery and completion
separately. No model polling or self-generated wakeup loop supplies progress.

For cloud/on-prem portability, qualify the same pinned API and adapter against
separate state/config/credentials with SQLite's supported single-writer runtime
ownership. Production multi-instance writers, Slack identity verification,
retention, rate limits and scaling require their own concrete design/evidence.
This document does not claim those capabilities exist.

No live Global restart, history replacement or accepted-database rollback is
required for contract design. A later implementation needs isolated candidate
tests, reviewed grants and an idle-boundary release preserving accepted and
unknown receipts. Keep code114 and existing owner-token clients compatible;
principal tokens must never silently inherit owner privileges.


### Canonical catalog activity

Each readable catalog room includes an additive activity object with version 1,
observedAt as Unix epoch milliseconds from the database statement timestamp,
and integer counts pending, claimed, running, cancelRequested, unknown,
and waitingForHuman. Counts cover viewer-visible unsettled deliveries originating
in that room. Private requests keep the same author/recipient/administrator
visibility as room detail. Waiting counts only viewer-authorized pending attention
for the current delivery generation and claim. It overlaps the delivery counts;
clients must not sum the categories.

The catalog uses one SQL snapshot, without a history scan or per-room HTTP reads.
An all-zero valid snapshot means no visible unsettled delivery at that observation.
It does not establish host connectivity or agent health. Missing, invalid or failed
activity reads must stay unavailable in clients, rather than becoming idle. The
timestamp is observation time, not work recency or an instruction to reorder rooms.
