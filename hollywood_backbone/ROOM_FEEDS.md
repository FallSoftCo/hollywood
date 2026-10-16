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
