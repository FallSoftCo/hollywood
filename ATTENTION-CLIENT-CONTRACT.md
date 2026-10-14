# Phone question answer contract

Pinned to deployed Hollywood 0.2.0, API v2. No new LAX capability or backend
rollout is required. Installed server handler ASTs for answer, command receipt,
pending inbox and detail match this checkout as inspected on 2026-10-14.

Capture the original environment/base URL and credentials reference, team ID,
task ID, question event ID and asking agent. Push supplies attentionId, team_id
(also team), task, author, kind and visibility; environment selection belongs to
the receiving device/profile route, not the currently selected app screen.
Verify the originating team's GET `/hollywood/v2/teams/{team}/messages/{id}` or
pending attention item before acting; approvals use their separate explicit flow.

Persist one commandId and exact answer body before network submission. POST
`/hollywood/v2/teams/{team}/attention/{questionId}/answer` with
`{commandId,body}`. commandId is 1–128 characters, body 1–3500 characters and
nonblank; the service also enforces its 6000 UTF-8 byte message limit. No task,
recipient, environment or answer field is accepted in that JSON body. The server
obtains task and recipient from the durable attention record.

A successful response is the persisted answer RoomEvent, not an envelope. Validate
author `you`, expected task, exact body, data.replyTo equal to question ID and
data.targets containing the asking agent. The original private scope is preserved.
This confirms answer acceptance: one atomic transaction stores the command receipt,
answer event, resolved question state/response and owner-context delivery. It does
not establish model consumption or work completion. Do not resolve locally on
notification tap, request transmission, timeout or transport failure.

GET `/hollywood/v2/teams/{team}/commands/{commandId}` returns
`{event,deliveries}`. Validate event correlation as above. Each delivery retains
task, agent, authority, state and turn. Confirmed delivery is a separate fact from
answer acceptance. A missing receipt (404) is not proof that an in-flight request
failed; retry only the same command/body, never invent a replacement command.
401 requires original-environment authentication recovery. Wrong team gives 400;
conflicting command or no-longer-open question gives 409. Retrying the accepted
same command/body returns the original answer without another dispatch.

Read question detail/pending state for reconciliation. The server state becomes
resolved when the answer is accepted, before model consumption. A question may
instead be dismissed or expired; 409 alone does not prove this answer was accepted.
If another command already resolved it, reconcile authoritative question state and
the exact answer receipt rather than showing a false send success.

Waiting-agent routing uses the original attention task/agent binding. It can queue
while unavailable; a later native turn resumes the stored thread. Unknown native
submission remains uncertain and must not be replayed blindly. This contract does
not promise a separate consumed-by-agent receipt. Native question/approval RPCs
are distinct from Hollywood attention and must not share an inferred answer ID.

Compatibility: these endpoints and fields remain valid for code114 and the
115/alpha97 candidate. Additive room-owner, goal and migration changes are not
prerequisites. Notification rendering and inline-action behavior need NextComp
qualification on the device; server/source inspection alone cannot establish them.
