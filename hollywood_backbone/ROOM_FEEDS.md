# Room conversation and execution feeds

Hollywood retains one canonical room event log. Deliberate human/agent
communication and native execution observations have separate read projections.
LAX captures actions; neither a command nor a tool completion is a chat post,
an execution request, a human instruction or independent acceptance of work.

## History and live contract

GET /hollywood/v3/rooms/{room}/history accepts view=all|conversation|activity
alongside the existing after/before and limit (1..100). The default all is the
combined canonical log. conversation excludes data.type=executionActivity;
activity selects those records. Filtering and authorization occur before the
page limit. Both projections retain original event IDs and canonical seq.
A busy execution stream therefore cannot consume the conversation page.

Every response identifies its view. Preserve independent cursors per authority,
room and view. before pages older records and returns olderCursor; after catches
up forward using nextCursor. The sequence is authority-wide and commit-ordered:
gaps are normal, and no cross-host causal ordering is claimed. nextCursor can
advance past records omitted by the projection or audience policy.

GET /hollywood/v3/rooms/{room}/events supports the same view. Existing clients
receive event: room entries. With batch=1, event: roomBatch carries one bounded
history page (at most 100 events); its SSE id is nextCursor. Empty checkpoint
batches may advance the cursor across irrelevant records. Last-Event-ID resumes
this exact projection. The stream drains available backlog without a one-second
delay between full pages, and rechecks token revocation and room access on every
page. Idle streams retain the existing one-second observation cadence.

## Concurrent action identity

Execution events retain data.type=executionActivity and add activityVersion=1
and actionId. The latter is action- plus SHA256 of canonical JSON
[deliveryId,generation,nativeTurn,itemId], using separators ',' and ':'.
It excludes phase: started/completed are immutable observations of one action,
not two commands. Historical records receive the same metadata at read time;
original receipts and stored records are not rewritten.

Keep agentId, hostId, deliveryId, generation, thread, turn, itemId, nativeType,
phase and the source event reference available in expanded inspection. Native
item IDs alone are not globally unique. The originating request fixes room and
audience; membership in another room does not copy actions there.

A client reduces observations by actionId. A completed observation supersedes a
started observation even if an old started record arrives later. Preserve both
original event IDs/sequences for inspection. Do not treat a later-arriving start
as evidence that a completed action restarted. Group adjacent activity into
condensed expandable execution blocks; individual agents remain attributable
inside those blocks. Batch render publication for bursts; no per-action push,
model wake or manufactured room message is needed.

Full native payloads remain in the protected execution journal. The public
activity projection retains existing bounded excerpts, redaction and tool
payload suppression. Read authorization is identical to the original event:
a private question or private source does not become visible to other members
through the activity projection. An agent sharing multiple rooms still retains
its own memory; use separate agents/contexts for separate trust domains.

## Unreported work is unfinished

A native execution may end before its worker submits the required work report.
New assignments carry workCompletionPolicy=report-required/v1 in their original
request. Assigned work with completed execution under that policy is eligible for its designated
reviewer and for enabled Global portfolio supervision. Existing material
sequence/digest and budget controls coalesce the review; unchanged observations
do not request another model turn. No semantic work state is rewritten merely
because a native turn ended, and no original execution is automatically replayed.

The reviewer must inspect original effects and missing evidence. Work cannot be
accepted directly from assigned state. Reporting after a settled assignment
still requires the appropriate original-ownership/reconciliation mechanism;
this change establishes visibility, not a general in-progress ownership transfer.
Historical work before a portfolio policy's activation is not resumed. Existing
assignments without the new completion policy do not gain a review obligation
merely because the service was upgraded.


### Conversational native candidate (2026-10-30)

New `message` and `request` publications are communication. Neither allocates
an execution delivery nor invokes the retired context/delegation scheduler.
Previously accepted operation receipts and their delivery identities are retained.
Native runtimes consume canonical events using their original room cursors.

Human inputs carry `data.humanInputEventId` equal to their canonical event ID.
Agent replies inherit the nearest human input through the canonical source chain;
this does not broaden an event's audience or make a private response public.
Agents may declare `responsePhase: message|update|answer|blocked`. The accepted
event carries `responseVersion: 1` and that phase. An answer means a deliberate
response is available, not that all related work has completed. Clients must not
infer response availability from executor shutdown. Absent phase remains absent
so old idempotency fingerprints are unchanged.

Routine `executorState` observations are activity, excluded before conversation
pagination limits. Their original canonical history remains available in activity
and combined views; they are not room chat notifications.

Communication administration (`principal`, `create_room`, `membership`, `role`)
requires the current authenticated administrator in the same domain transaction,
but no execution-delivery source. Supplying legacy native provenance still invokes
its existing validation. This does not grant administrator status, host access,
worker execution or general business mutation rights. Existing operation receipts
remain actor-bound, idempotent and inspectable through administration/commands.

## Claimless native action observations

`POST rooms/{room}/activity` accepts an authenticated agent's immutable native
observation with operationId, original sourceEventId, nativeThread, nativeTurn,
itemId, phase, nativeItemDigest and the bounded ActivityItem. This is the activity
feed's write path, separate from messages; it creates neither a delivery nor a
model input. The original event must actually have addressed this agent in this
room. Direct source audience remains direct to that source's author. The runtime
attests native execution; this is not independent host-process verification.
`conversationVersion:1` observations retain the same activityVersion1 action card
shape, with an actionId bound to authority/agent/thread/turn/item instead of a
claimed delivery. Original delivery-backed observations and receipts are unchanged.
Exact operation retries return the original receipt. Changed contents conflict.
Activity history/catalog use the existing projections; conversation excludes it.
Native runtimes retain raw tool payloads privately and publish bounded structural
labels, never arbitrary read-tool results or credentials. Observation retries
cannot start native work. This contract does not yet supply native liveness or
replace delivery-based room activity counts; clients must not infer idle from it.

`POST rooms/{room}/activity` also accepts `observationType:nativeState`,
`nativeThread`, monotonic persisted `revision`, optional original `sourceEventId`,
and `status:{type,activeFlags}`. Types are native idle/active/systemError/notLoaded
or unavailable when current room attribution is not established. Active requires
its actual addressed input. This updates an expiring snapshot; it appends NO room
event, creates NO delivery/executor and starts NO work. Equal revisions return
the original observation time; stale/changed revisions conflict. A retry cannot
extend liveness using an old acknowledgement.

Catalog `nativeState` v1 includes server observedAt, runtimeCount and visible
agents with nativeThread/revision/observedAt/expiresAt/status. The batched catalog
applies original private-source audience before projecting status. Missing private
observations must not be interpreted as idle. After75seconds without a fresh
observation, status becomes unavailable. Room activity counts remain legacy data;
clients with native observations use the native lane instead of treating absent
delivery claims as idle. Native state is authenticated agent attestation, not
independent proof of host processes or task completion. No scheduling, execution
ownership, automatic continuation or recovery is performed by this snapshot table.
