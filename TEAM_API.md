# Resource-scaled teams API

Coordinator-native `hollywood_provision` accepts `task, agent, role, source, request_id`.
It registers one teammate and joins an existing room in the coordinator's team.
`source` must identify a public human message in the calling room; the coordinator
must select a message explicitly authorizing this setup. Peer messages and private
contexts cannot authorize provisioning. Existing identities must match team and
role exactly. Registration and membership create no deliveries, bindings, threads,
filesystem permissions or execution grants. Work still requires separate assignment.

Reuse `request_id` after an uncertain result: a namespaced command digest returns
the original audited receipt, including stable actor and room IDs, across restart.
Changed contents conflict; existing command receipts and room history are retained.

Provisioning also records an explicit accountable room owner in additive
`room_owner` metadata. A conflicting owner is rejected. Joining a room alone never
sets its owner. Room detail adds nullable `owner` (agent ID). Unaddressed human
messages in the team lobby select its coordinator. Other rooms require an
explicit owner or reject the unnamed send with a recipient-selection error.
Unaddressed human messages select that explicit owner. Explicit recipients, mentions and reply correlations take
precedence. Retried accepted commands retain their original recipients across
routing upgrades. Tool summaries retain tool authority and do not become human
requests. Existing provision receipts are returned unchanged; deployments must
review explicit owner mappings for previously provisioned rooms rather than infer
ownership from membership or silently rewrite old receipts.

Assignments now persist `work_order` rows keyed by assignment event and owner,
with original human source, owning task, dependency event IDs and acceptance text.
`hollywood_assign` adds optional `dependencies` (up to 16 same-team work-order
event IDs) and `acceptance` (up to 2000 characters). Existing calls retain their
schema and command digests. A nonempty dependency list records the order with
`admission: held_dependencies` and creates no execution deliveries. Automatic
dependency acceptance/release awaits the native goal contract; it is not supplied
by room membership, copied history or a model's completion assertion.

Owner `blocked`/`completed` reports remain durable in the owning room with task
and owner metadata. They do not automatically queue coordinator turns in Global
or a parent room. Global reads the owning room for results and questions. Explicit
cross-room communication requires a scoped reason; status publication does not
implicitly mirror messages, memberships or execution contexts.

Peer reports addressed to the coordinator in the team lobby are observation-only:
even `response: required` does not admit a coordinator turn there.
`hollywood_brief` publishes in the owning context without starting a coordinator
turn. Human explicit recipients/direct addressing are unchanged. Accepted older
tool commands retain their original targets/receipts on retry without redispatch.
Client defaults must distinguish lobby coordinator selection from an explicit
human recipient choice; room membership lists are not broadcast recipient lists.

Authoritative implementation contract; legacy `/hollywood/v2/...` conversation routes select `default`. New team conversation routes use `/hollywood/v2/teams/{teamId}/{same-suffix}`. Bearer token identifies the environment owner, who can access all teams. Agent tools are restricted to their own team and private identity.

- `GET /hollywood/v2/teams` returns `{teams:[{id,name,coordinator,lobby,members:[agentId]}]}`. IDs are globally unique slugs. Existing default team retains `lobby`, coordinator/maya/theo/rowan. New team lobby is `team-{teamId}`.
- `POST /hollywood/v2/teams` accepts `{id,name,coordinator,role}` and returns the created team object above. Team ID max 59 characters, coordinator ID max 64. Repeating identical creation is safe; conflicting IDs return 409.
- `POST /hollywood/v2/teams/{teamId}/teammates` accepts `{id,role}` and returns `{id,team,role}`. IDs globally unique, max 64 characters. Identical retries safe.
- Scoped `/overview` retains tasks/agents/attention/deliveries/cursor, with `team:{agentId:role}`, `teamId`, `lobby`, and `capacity:{limit,active,queued,reason,availableMemoryBytes,cpuSlots}`. Capacity is environment-wide; teams and tasks are persisted without a numeric ceiling. `tasks[].team` identifies origin. Capacity limits running work only.
- Scoped `/messages`, `/history`, `/events`, `/commands/{id}`, `/control`, `/attention/{id}/answer`, `/approval/{id}/answer` retain existing request/response payloads. Every task/event/member is validated against selected team. Posting task `lobby` maps to selected team's lobby; history requests should use returned `lobby`.
- `GET /hollywood/v2/attention` returns pending events across all teams/tasks for environment owner. Scoped `/teams/{teamId}/attention` filters one team. Response `{data:[{...RoomEvent,attentionState:'open',team_id,task_id,team_name,task_title}],total_pending,next_cursor}`. Query `cursor` (event ID) and `limit` (1–100); items ordered by ID. Client places the pending inbox before room switching controls; FIFO pending items across kinds is acceptable. Load more pages on demand; total count is independent of fetched pages. Legacy aliases `total`,`nextCursor`, query `after` are also accepted.
- Answer using originating team's `/attention/{id}/answer` with existing `{commandId,body}`. Returns persisted reply event; only successful response clears pending. Retry same command/body safely. Approvals retain explicit approval endpoint. Never substitute `{answer}`.

Draft/cache/navigation/stream cursor keys: environment + team + task + room/direct recipient. Answering inbox must capture originating environment/team/id and preserve current conversation/draft.

Discovery pagination: GET `/teams?cursor={lastId}&limit=100` returns `teams,total,next_cursor`. GET scoped `/teammates?cursor={lastId}&limit=100` returns `team:{id:role},total,next_cursor`. Both cap pages at 100, never stored identities. Team objects include `members_total,members_next_cursor` for their bounded member preview. Overview includes first roster page plus `teamTotal,teamNextCursor`, first team page plus `teamsNextCursor`. Clients follow cursors to enumerate the full chooser/roster.

Idle memory: after a persisted turn completes, Hollywood unsubscribes that thread and resumes it before future work. Codex's app-server performs idle unloading after its own delay (30 minutes in current local protocol implementation). Older servers without unsubscribe retain idle memory; resource pressure pauses admissions and never kills active turns. Memory budget is an admission estimate, not a process sandbox limit. Pending starts are reserved separately and cannot create free-memory credits.

Explicit fresh task: `POST /teams/{teamId}/tasks` (legacy `/tasks` for default) accepts `{id,title,commandId,body,targets?:[]}` and returns the initial persisted human RoomEvent with 202. Omitted targets select the registered team coordinator. Repeating identical command is safe. Globally unique task ID max 64, title max 100; body uses existing message bounds. This creates fresh bindings/tool definitions, retains all old histories, and establishes this user request as delegation authority. A continuation can cite previous task/event history in its initial body.

Compatibility: persisted legacy Codex threads retain their original four-member dynamic tool schema because current `thread/resume` cannot update `dynamicTools`. New tasks/teams start with unrestricted valid agent-ID schemas and paged roster discovery. Existing transcripts/bindings are never silently replaced. Use explicit New task for dynamic-member work in a legacy team. Runtime start/resume remains full-access with approvalPolicy=never.

Push registrations are environment-wide. Data payload includes `team_id` and `team` alongside task/attentionId; use the original team scope for notification detail and answer requests.

A legacy coordinator can also bootstrap without new tools: call the existing `hollywood_assign` with `agent="coordinator"` and a fresh task ID. That binding starts a new thread with current dynamic tools, then discovers/assigns custom members. Original lobby transcript and binding stay intact. A regression verifies this authorization-preserving path. This applies to the original default team's `coordinator`; newly created teams already start with current schemas and use their registered coordinator IDs.

## In-app Android update distribution

Environment-owner authenticated routes (use environment base URL, not team prefix):

- `GET /hollywood/v2/update` returns `{available:false}` when nothing is published. Otherwise `{available:true,packageName,versionCode,versionName,sha256,size,signingCertificateSha256,apkPath}`. `apkPath` is always `/hollywood/v2/update/apk/{sha256}`; resolve only against the same environment origin and send the existing bearer token.
- `GET /hollywood/v2/update/apk/{sha256}` returns the immutable APK, Android package MIME type, content length/range support. Both endpoints require auth and use `Cache-Control: private, no-store`. Invalid/corrupted publication yields 503; unknown release yields 404. No redirect/external download URL is supplied.
- Client compares versionCode to installed version, enforces package `co.fallsoft.losangelex`, verifies expected byte count/SHA256, and checks APK signing certificate against its installed app certificate before invoking Android's installer consent. Manifest certificate is descriptive; installed signature remains the trust anchor. Partial/failed downloads must never invoke installation. No ADB is required after this updater bootstrap is installed.

Backend option `--android-updates DIR` defaults to `STATE/android-updates`. Publishing artifacts there is hot; future APK releases need no runtime restart. Reuse the existing signed APK from `~/.local/share/losangelex/android-releases` as publisher input; existing `/android/...apk` links can remain.

Publisher, run where Android SDK tools and signed APK exist:

```
uv run --extra room python -m hollywood_room.updates --apk SIGNED_APK --directory OUTPUT_DIR --aapt AAPT_PATH --apksigner APKSIGNER_PATH --expected-signer INSTALLED_CERT_SHA256
```

It verifies a private APK snapshot with apksigner, reads real package/version with aapt, checks the expected installed signing certificate, rejects downgrade/same-version replacement, then writes `{sha256}.apk`, `{sha256}.json`, and atomically replaces `current.json`. Preserve the original release artifact and synchronize immutable files before the current pointer on another host. The known existing release certificate in repository evidence is `c98c28d5e349d270823d6c9def8dbe0f6ad718e6d2fc85b404d3a6b21ed9168b`; final publication must verify it against the actual installed/release signer, not merely trust this document.

## Dismissing stale questions

`POST /hollywood/v2/teams/{teamId}/attention/{id}/dismiss` accepts `{commandId}` and returns `{id,state,dismissed:true}` with server-confirmed state `dismissed`, `resolved`, or `expired`. Legacy route addresses only the default team. Capture the original environment/team/ID; never use the selected conversation implicitly.

Open questions become `dismissed` in one transaction with an idempotent command receipt and private-scope-preserving audit event. Original question/history remains; dismissal creates no answer and dispatches no agent turn. Resolved/expired items keep their terminal state, answer text, and task status while gaining a durable card-dismissal marker. The first dismissal adds one private-scope-preserving audit event; repeated dismissals add no further audit events. Command IDs cannot be reused for a different item/action. Failed requests leave server state unchanged and can retry the same command ID. The client hides dismissed cards immediately with a durable local tombstone and silently queues origin-bound sync; transient errors never resurrect the card or show a dismissal-uncertainty alert. Pending totals exclude dismissed items; scoped `GET /messages/{id}` exposes `attentionState` for cache reconciliation.

Open approvals require the explicit review/decline flow and return 409 from dismiss; closed approvals can reconcile their terminal state. In addition, an explicit normal chat reply (`replyTo`) addressed to the asking agent now resolves an open question atomically with its actual answer. Unthreaded chat messages are not inferred to answer questions. Local hidden-card state is distinct from server resolution: swipe/Dismiss persists a local tombstone immediately; server confirmation records synchronization but does not gate hiding. Authoritative totals remain server truth while UI may separately account for locally hidden items.

Card-dismissal extension: `attention_dismissal` stores a per-attention timestamp separately from answer/task state (additive table, no destructive migration). Existing `state=dismissed` rows are backfilled. Detail `/messages/{id}` and history annotate both the original attention and answer/receipt cards whose `replyTo` or `data.attention` points to it with `attentionId,attentionState,dismissed,dismissedAt`. Swipe actions on answer cards use `attentionId` (or original question ID from `replyTo`) and the existing attention dismiss endpoint. Messages remain in history; clients may hide their attention cards using this marker. Open approvals still require explicit review. Legacy clients ignoring added fields keep existing behavior.

## Reviewed obsolete-delivery supersession

Before the retired-budget retry migration, Store optionally reads the owner-published file `room.db.supersessions.json` beside its DB. This is an operational plan, never an HTTP/client-supplied request. Missing file leaves normal retry behavior unchanged. The plan is `{version:1,supersessions:[{deliveryId,eventId,task,agent,sourceSha256,evidence:[{id,sha256}],reason}]}`. Fingerprints use `hollywood_room.recovery.event_fingerprint()` over full decoded event records, including IDs, author, body, data, visibility and timestamp.

Every source/identity and later public same-task completion evidence must match exactly. The delivery must still be uncertain, have no turn, and carry exactly the retired automatic-budget error. A matching plan marks that one row `superseded` and writes `deliverySuperseded` with source/evidence IDs, reason, previous error and plan hash. Existing event/history rows are never deleted; other eligible failures still requeue normally. Replaying the same plan requires its existing matching audit and adds nothing. Any mismatch fails activation before retrying work. Publish this plan only after human-authorized completion review, then validate backup-copy activation before live safe-idle rollout. Recovery/rollout owner remains responsible for clearing alerts only after confirmed recovery or supersession.

## Actionable delivery failures

Internal `deliveryFailure` events expose `data.deliveryFailure` with `deliveryId,commandId,sourceEventId,sourcePreview,sourceAuthor,teamId,teamName,task,taskTitle,recipient,phase,state,executionState,reason,errorType,retryable`. New delivery failures never create human-attention items or push alerts and are excluded from normal history card queries. Internal detail/replay diagnostics remain restricted to original visibility/team. Existing legacy notices may expose context until exact audited retirement. `sourcePreview` is a bounded excerpt of the originating request. `phase` identifies preparation vs starting/steering a turn; `executionState` distinguishes known-not-started from transport uncertainty. Failures after a turn/start or turn/steer attempt never advertise safe blind retry. Retired-budget failures are managed by server recovery and likewise do not encourage a duplicate send.

These are diagnostic fields, not a new retry endpoint. Readers refresh the delivery state so queued recovery, confirmed delivery and audited supersession are shown accurately. Legacy generic failure cards are enriched only when a matching delivery is uniquely attributable; otherwise source IDs/previews remain null and known task/recipient/reason remain visible. Raw history is never rewritten. No public error card inherits a private source preview. The obsolete automatic depth/turn cutoff itself is removed by the existing reviewed runtime fix; exact historical recovery and supersession remain rollout-owner operations.

Overview capabilities now advertise `attentionDismissal`, `attentionCardDismissal`, and `deliveryFailureContext`. Older servers may omit these fields. Clients retain queued dismissal tombstones while an origin lacks the route/marker capability, retry silently with the same command ID after upgrade, and never claim unconfirmed server success. The backend does not create a human-attention item for a failed dismissal request. Open approvals still require explicit review; card dismissal does not approve/decline work.

Superseding user policy: delivery uncertainty/retired-budget errors are internal diagnostics, never persistent human-question alerts. Operational plans may add `retireDeliveryAlerts:[{id,sha256}]` (supersessions optional). Each exact fingerprint must match a legacy generic delivery notice or structured delivery-failure attention event; unrelated questions/approvals are rejected. The transaction marks only an open matched notice resolved, sets its card-dismissal marker, and records private-scope-preserving `deliveryAlertRetired`. It neither sends an answer nor changes underlying delivery/task state. Original history remains; repeated plans add no audit duplicates. Recovery of genuine work and supersession of obsolete work remain separate actions.
