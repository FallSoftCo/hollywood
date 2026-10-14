# Hollywood backbone candidate

Hollywood owns room and communication identity. No LAX process, model provider,
TUI or Android client is imported into the authority service. LAX owns the native
execution connector in the LAX repository, with a pinned app-server and local
intent journal. NextComp integration is a subsequent release gate.

## Communication and execution

A room has a stable authority/room identity, purpose and accountable agent owner.
Membership carries explicit read/post/request grants. `message` publishes to an
audience without admitting execution. `request` addresses an explicit agent or
the room owner and atomically persists its event and one execution delivery.
Observation through history/streaming does not create a context. Explicit agent
requests retain agent authorship and source references; the LAX connector injects
those as native tool authority rather than inventing a direct human instruction.

Every HTTP operation derives its principal from the bearer credential. Client
JSON cannot select an authoritative principal or enterprise. Administrators have
all room rights within their authority, including private visibility, matching
this deployment policy. Worker room grants do not grant host/filesystem access.
That separate execution boundary is not implemented by this candidate yet.

PostgreSQL commits events and execution intent together. The authority sequence
is allocated under an authority-row transaction lock, so replay order agrees
with commit order. Command IDs are scoped to the authenticated principal and a
canonical input digest. Changed input conflicts; retry resolves the original
accepted target. Receipts and history recheck current grants. Streams recheck
both room permissions and credential revocation.

Connectors must be enrolled for an agent and host. Concurrent claims serialize
per actor; only one pending delivery is admitted while that actor has claimed,
running, unknown or cancellation-pending work. Claim IDs and owner generations
survive server restart. No timeout, disconnect or absence of a process promotes
an unknown delivery back to pending. An unknown native effect requires native
readback/operator reconciliation. Current finite CLI refuses to replay it.

`cancelled` before admission and `cancel_requested` after admission are separate.
A connector acknowledgement can establish execution stoppage but never external
effect rollback. Live native cancellation monitoring remains an explicit gate.
Native turn completion is not business acceptance. Results/acceptance and actual
artifact evidence must remain separately attributable.

## Migration and release

The live v2 SQLite runtime is untouched. Before cutover, import its authority,
room and actor IDs, event/command aliases, bindings, paused flags and unconfirmed
receipts under a coordinated writer-release receipt. Never redispatch accepted
messages to repair their old recipients. Historical numeric events need explicit
alias mapping to stable v3 events; existing client receipt references must remain
resolvable. Do not turn imported membership into execution capability.

A pinned v3 compatibility boundary must preserve existing device pairing,
environment identity, drafts, unknown commands, question/approval scope and voice
origins while NextComp moves to the canonical API. No copied credential grants
another enterprise owner rights. No metadata mapping silently attaches a live
native thread; ownership release and native thread continuity are separate facts.

Bootstrap currently creates only the durable communication authority, one Global
room, and human/Global identities. Protected installation intent is fsynced before
DB mutation so retry retains identity and credentials. Full installation still
requires actual Global host administration, isolated worker capabilities, host
enrollment, doctor checks, backups and the deployment package. Role metadata alone
is not evidence of administrator execution capability.

## Evidence and limits

The disposable PostgreSQL/API scenario passed ten integration gates including
concurrent replay, private audience filtering, competing claims, cancellation,
restart retention and revoked access. Its execution acknowledgements were fixtures.
A separate actual LAX integration used three finite gpt-6.1-sol/xhigh turns:
Global delegated to Engineering, Engineering wrote and checked a real file and
published in its owning room, and Global read and summarized that report without
creating another execution context there. Both agents used native LAX app-server,
not a TUI. Receipts are in ../repair-release-20261014 relative to the workroot.

Not yet qualified: resident connectors and fairness, native crash reconciliation,
live cancellation, human questions/approvals, physical phone voice/push, second
host execution/artifact retrieval, full Global administration, migration/rollback,
principal-bound OS execution isolation, scale/availability and fresh installation.
This candidate is not production-ready or an architecture-complete claim.
