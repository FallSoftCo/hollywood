# Coordinator provisioning candidate

This candidate retains Hollywood API v2, package 0.2.0 and the existing database
schema version, with an additive `tool_receipt` table for reconnect safety.
It adds `hollywood_provision` to the native tool manifest. Phone
code114 requires no new request fields or routes. Existing commands and receipt
lookup remain unchanged. Provision receipts use the existing command/event tables
with a `provision:` namespace and a `provision` audit event kind.

Registration and room membership create no model turn, binding, work assignment,
filesystem permission or execution grant. The caller must be the team coordinator
in a public room and cite a public human message in that room. Selecting a message
whose content explicitly authorizes setup remains the coordinator's responsibility;
the server validates provenance rather than interpreting natural language consent.

## Persisted Global tool availability

Hollywood currently supplies `dynamicTools` on `thread/start` only. The checked-in
LAX `ThreadResumeParams` protocol has no corresponding field. Consequently an
ordinary service restart or resume does not make this tool available in an
existing Global thread. Do not replace that thread or its history to add tools.
LAX must first provide a validated, persistent tool refresh contract that refuses
active-turn mutation and preserves thread identity and execution settings.
Hollywood integration and its verification remain a dependency on that contract.
The existing authenticated HTTP registration/join routes remain available.

## Proposed continuity sequence for Global review

1. Record exact candidate source/artifact digests and existing deployed binaries,
   configuration, thread IDs, DB path, pending command IDs and uncertain receipts.
2. Qualify the LAX refresh contract and Hollywood integration against disposable
   persisted threads; verify old API/code114 behavior and tool receipt recovery.
3. Agree an explicit admission boundary with Global. Finish active turns without
   interruption; do not replay unknown tool effects or uncertain deliveries.
4. Take a consistent database backup with its journal handled by SQLite, retain
   the accepted database in place, and preserve account/model/permission/pairing
   configuration. Only a separately authorized rollout may restart services.
5. Refresh the original Global thread at the supported idle boundary. Verify the
   same thread/history, accepted tool manifest, settings and original receipts
   before admitting new work. A failed or uncertain refresh blocks tool admission
   pending reconciliation; it does not trigger a new thread or model turn.
6. If rollback is necessary, restore the reviewed code/binary version while
   retaining the accepted database and receipt history. Never restore an older DB
   over newly accepted commands. Account for persisted tool manifests explicitly:
   old code may not handle new tool names, so reconciliation must precede admission.

No live restart, tool refresh, thread replacement or database migration has been
performed as part of this candidate.

## Owner progress audit extension

Superseding room-local policy (#2532): task status no longer creates automatic
Global/parent-room deliveries. Earlier Global-notification descriptions below
describe an intermediate candidate, not the current implementation. Results,
blockers and questions stay in their owning rooms for Global to read.

`hollywood_ask` now stores its tool command receipt atomically with the question
and push outbox entry. Retrying the same call returns the original attention ID;
changed contents conflict. `hollywood_task` similarly deduplicates status events,
push entries and coordinator deliveries. Public owner blocked/completed reports
in pre-created rooms without a distinct parent task route to the team lobby's
coordinator. Private status reports do not gain a public delivery.

Focused fixture verification covers assignment retry, question retry/conflict,
database reopen, reply routing to the original owner/task, answer retry without a
second dispatch, wrong-team rejection and Global blocker/completion delivery.
Provider push tests verify high-priority question payloads and omit resolved or
historical questions. They do not establish handset notification rendering or
reply-action success. NextComp owns that qualification. LAX owns persisted-thread
lifecycle and tool-refresh qualification. No polling or wakeup timer was added.

The previously published wheel digest predates this audit extension. A replacement
artifact must be built and identified before any rollout of these additional fixes.

## Pending native tool-call replay

The generic runtime guard persists `(threadId, callId)` plus a digest of turn,
tool name and arguments before executing a dynamic tool. Settled responses return
from that receipt even without an active turn. A changed payload conflicts. A
receipt without a response stays unknown across restart and refuses both replay
and new dynamic tool calls in that thread until reconciliation. No automatic
receipt deletion, inferred failure or effect replay is implemented.

This protects calls first admitted through the new guard. Older pending calls
without a generic receipt cannot be retroactively classified as fresh or settled.
Before rollout, reconcile existing native pending requests against original
action receipts; do not admit an unclassified legacy request. Approval requests
continue through their separate approval lifecycle. The added table must remain
in the accepted database on rollback; old code cannot provide this guard, so
resuming pending requests under old code requires explicit reconciliation.

The owner-progress wheel also predates this generic guard and must not be deployed
as the complete reconnect-safety candidate. Focused tests simulate settled replay,
process reopen, changed payload and effect-without-receipt refusal; they are not a
live app-server reconnect qualification.
