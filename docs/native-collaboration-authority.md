# Native collaboration and the canonical authority

This is the first server stage, based on `56fb68c11a331905d9a81770dd8093a95deb4212`.
It implements physical path claims, explicit handoff and canonical receipts.
Native bridge integration, edit plans, patch permits and child admission remain
unavailable. Nothing here establishes restored native behavior or deploys a schema.
The report-recovery feature and its pending real-native proof remain intact.

## Pinned feature inventory

The comparison reads Git objects from preserved fork
`c9bd503957c0a5c6687f5cf4766bf0c4397e0746` and updated maintained source
`6a769c732b387a0a755eeaf644f855ccde4dc5fc`. The transferred merge/index is owned
by LAX and was never edited by this server task. Exact exported object hashes
are in the owned RAM `pinned-source-manifest.json`.

| Mechanism in the preserved fork | Actual entry and behavior | Canonical adaptation / current status |
| --- | --- | --- |
| `collaborative_edit_plan` | `core/src/tools/handlers/collaborative_edit.rs`, `tools/losangelex_spec.rs`; records file, slice, intent, peers, handoff, integrator and report-back in local state; optional `/hollywood/v1/messages` ambient notification can fail after local success | Preserve the real native handler/spec. Enterprise mode needs canonical plan consent and revision/content-bound slices; local SQLite and chat cannot grant permission. Plan port is specified below and **not implemented** by this claims stage. |
| Edit-plan lease | `state/src/model/collaborative_edit.rs`, `state/src/runtime/collaborative_edit.rs`, migration0051; absolute local file path and actor thread, default3600-second expiry, query up to20 active exact-file plans | Canonical host/workspace/path plus explicit source base, canonical participant work IDs, CAS, durable release/handoff and uncertain-state holds. No thread-name authority. |
| `apply_patch` collaboration preflight | `handlers/apply_patch_collaboration.rs`; called from both patch paths in `handlers/apply_patch.rs`; compares peer directory/file claims and matching plans; actor/peer aliases can authorize; missing DB/read failures skip checks | Retain actual preflight at both native call sites. Canonical enterprise mode must fail closed, obtain a fresh permit and check actual bytes immediately before mutation. A claims inspection does **not** issue a permit. Standalone private mode may retain the original local mechanism explicitly. |
| Path claims | `state/src/runtime/path_claims.rs`; SQLite immediate transaction, absolute-path file/directory conflicts; expiry deletes claims | First implemented port uses canonical physical namespace and relative literal segments. Expired ownership stays held. Same-file slice collaboration is a later explicit plan/permit extension, not a permanent prohibition on collaborative editing. |
| `coordination_act` / task listing | `handlers/coordination.rs`, `state/src/model/coordination.rs`, runtime/coordination.rs and migration0045; open/award/accept/done/cancel/handoff/yield, dependency unblocking, duplicate scope/lane checks, claimed paths, concrete summaries and optional notifications/wakes | Commitments project existing goal/work/cooperation/decision/attempt/receipt state. Do not create a second task table. Native act names remain real dispatch, with explicit mappings below; unsupported transitions hold. |
| Task lease expiry | `runtime/coordination.rs::reconcile_coordination_task_leases`; releases effective paths, clears owner and reopens expired active tasks | Disable this shared-mode behavior. Canonical unknown/unsettled work remains owned; inspect original receipts and use only existing qualified recovery/rework rules. Time is not evidence of non-execution. |
| `losangelex_team_launch` | `handlers/hollywood.rs`; connects to app-server, starts peer threads/turns by default, inherits workspace/room/model settings | Enterprise admission must select eligible canonical agents and existing planned work, then receive normal owned execution receipts. No unaccountable app-server child starts. Actual native handler remains; canonical child admission is not implemented here. |
| `hollywood_team_up/status/member_update` | same handler; v1 teams/team-members use session IDs, aliases and role/state fields | Project canonical goal cooperation (planner/executors/verifier), principal roles and actual delivery/executor state. Observation or team metadata never starts work. |
| Native multi-agent tools | `handlers/multi_agents.rs` and child handlers; `AgentControl` spawn/resume/input/wait/close, inherited runtime provider/sandbox/cwd; updated source also has `multi_agents_v2` | Preserve private bounded subagents as explicitly private proposals, or route shared peers through canonical owned admission. Shared effects require the child's own authorized binding, never an invented parent identity. |
| Old Hollywood transport | `core/src/hollywood.rs`, `app-server/src/hollywood.rs` and native handlers; loopbackv1 registry/messages/teams, thread aliases, attachment/wake policies | v3 authenticated rooms/requests, durable receipts, captured participation, passive observations and current sourced decisions. No v1 success fallback in enterprise mode. |

The updated native tree lacks the named fork collaboration/coordination/old
Hollywood modules above; the connector currently forces `multi_agent` and
`multi_agent_v2` false. A compiled merge, restored strings, guidance or changing
those booleans alone cannot establish feature restoration.

## Trust boundary and native bridge

Native built-in handlers and patch preflight must remain native mechanisms.
The enterprise adapter requires a new captured reverse request on the actual
app-server channel, proposed v2 method `item/collaboration/call`:

```text
CollaborationCallParams = {threadId, turnId, callId, tool, arguments}
CollaborationCallResponse = {success, result, canonicalReceipt, holdReason}
```

Thread/turn/call/tool are supplied by the native runtime invocation. `arguments`
are model intent, without authority identity. A protected LAX bridge must own or
authenticate that channel and the original native process incarnation, wait for
the authoritative running receipt, and derive agent/host/context/workspace,
claim/generation and request from the captured canonical execution. It must verify
the sandbox/root descriptor, normalized paths, actual source revision and bytes.
It journals the exact call/operation before mutation and preserves unknown replies.

The current connector's `item/tool/call`, tool-admission barrier, effect journal
and authenticated source-v2 transport are useful integration seams. They do not
by themselves authenticate a stronger built-in channel merely because a request
contains the same fields. A model-held worker bearer credential can reproduce
those fields. This stage therefore accepts claim effects **only** from a service
principal that is the current enrolled host controller and has separate explicit
bridge enrollment. Worker/human/admin bearer credentials cannot call the effect
relay by supplying identities, even with a valid source header.

The controller credential/bridge endpoint must stay outside model-readable state
and must not expose a generic signing/forwarding RPC. The bridge may attest only
callbacks received from its captured native channel. Root/host administration is
in the trust base; this is not protection against a compromised host administrator.
The server checks actual canonical lifecycle/binding/resource records and the
administrator's enrollment; it cannot inspect the remote RPC pipe or filesystem.
The protected bridge/channel implementation is still a LAX integration dependency.
No such runtime enrollment was created by this task.

## Implemented wire: physical claims

All paths below are relative to `/hollywood/v3/`. Strict schemas are defined in
`hollywood_backbone/collaboration_models.py`; unknown fields are rejected.

| Route | Authorization and request |
| --- | --- |
| `PUT hosts/{host}/collaboration-bridge` | Existing administrator authorization, with normal native source when caller is an agent. `operationId, revision, controllerId, state(active/revoked), protocol(canonical-physical-claims/v1), adapterSha256, enrollmentSha256`. Controller must match current host admission. Enrollment uses existing administrative command receipts and CAS. |
| `GET hosts/{host}/collaboration-bridge` | Current enrolled service or administrator; returns policy plus `editPlans=false, patchPermits=false, nativeChildAdmission=false`. |
| `POST collaboration/path-claims` | Current enrolled service only; exact envelope below. Shares the exclusive authority fence with cancellation/revocation. |
| `GET collaboration/commands/{agent}/{operation}` | Current enrolled service for the original host, active target actor and current event read rights. Returns original wire/digest/acceptedResult only, including after original participation ends. No callback, mutation or model is repeated. |

The effect envelope is:

```text
{operationId, bridgeRevision, source, executorId, descriptorDigest,
 serviceBindingDigest, channel:"captured-app-server-rpc/v1", command}
```

`source` is exactly `hollywood-native-source/v2`. It comes from the protected
bridge, not native arguments; `source.tool` preserves the actual native tool name.
This stage accepts `coordination_act`, and `apply_patch` only for `inspect`.
It rejects `collaborative_edit_plan`, since a claim is not an edit plan.
The descriptor, fresh independent live observation and service binding must match
the current attached executor and current controller. Persistent binding supplies
workspace identity; no command has agent/host/workspace override fields.

Every command has `action, workId, workRevision, claimId, claimRevision,
briefingSequence, note`. Revisions are strict integers; current sourced decisions,
posting rights, placement, cooperation and cancellation are rechecked, including
on idempotent replay. Additional fields and behavior:

| action | Additional fields / behavior |
| --- | --- |
| `acquire` | `claimRevision=0, relativePath, kind(file/directory), sourceRevision(40/64 lowercase hex immutable revision), contentSha256(64 hex or absent), leaseSeconds(1..3600)`. Original **current** work admission is required. Every overlapping unreleased claim blocks, including same-owner duplicates and expired claims. |
| `renew` | `leaseSeconds`; exact current owner/work/claim revision and a still-active unexpired lease. Immutable source/path/content base does not change. |
| `inspect` | Exact revision and current work admission. Historical snapshot only, with `patchPermitIssued=false`; never an authorization to patch. A new real preflight/permit port is required before enterprise writes. |
| `release` | `effectDisposition(reviewed/unknown)`; only reviewed disposition without unresolved original attention/control can release. Explicit later normal participation by the same owner/binding can clean up paused/cancelled original work. A cancelled current source itself cannot authorize effects. |
| `handoff` | `targetWorkId, targetWorkRevision, effectDisposition`; reviewed explicit offer to a distinct already assigned authorized peer work item in the same goal and physical namespace. No task owner changes or new execution. The offer holds the path. |
| `accept` | `leaseSeconds`; exact target work's current native admission and claim revision accept the offer. Original owner/work/role/placement/consent must still be valid and its execution cannot be unknown or cancelled. Only then does claim ownership move. The new lease records the recipient's actual participation; prior receipts retain the original lineage. No automatic lease takeover. |

Claim identity is `(authority, host, workspace, normalized relative path/kind)`.
Paths are NFC, literal POSIX segments; absolute/drive paths, dot/traversal/empty
segments, backslashes and controls are rejected. `%` and `_` are literal names.
File/directory overlap uses segment boundaries, not SQL glob expansion.
The protected bridge must register the same physical root under the same workspace
identity, resolve symlinks/aliases and pin descriptors. The server does not infer
physical equivalence from textual roots or manufacture a filesystem registry.

Different hosts/checkouts remain different physical claims, even if IDs or paths
otherwise resemble one another. A logical shared-source mapping must be explicitly
authorized with its own mapping revision, repository identity and source/content
expectations. Cross-host collaboration exchanges hashed patch/commit artifacts;
the integrator owns and patches its own physical checkout. This stage rejects
cross-namespace claim handoff; it does not transfer files or silently merge roots.

Every accepted call reuses `hb_command`, canonical room events and
`hb_native_effect`. Same operation/payload returns the original receipt; changed
payload conflicts. Receipts are historical, not renewable permits. Claim history
is retained in immutable accepted receipts; no expiry deletion occurs. An unknown
POST result is resolved by original command GET only. No task, attempt, delivery,
context, execution binding or model is created by claim operations.

## Task / team adapter mapping

| Native intent | Exact canonical meaning |
| --- | --- |
| `open_task` | `goal.work` in an existing sourced goal, its current revision, explicit room/acceptance/dependencies and cooperative owner eligibility. Planner authority is required. Direct award subsequently uses ordinary `work.assign`; open Kanban work remains unowned until explicit eligible claim. `team_id` projects goal/cooperation identity, not another team/task store. |
| `accept` | Existing assignment acknowledgment or eligible `work.claim` for open Kanban work. Claim creates a **separate** owned native execution; the coordination participation ends and lets that assignment run. Do not implement the claimed task in an unrelated native turn. |
| `done` | Current original participation invokes ordinary `work.report` with approved connector-hashed evidence. Project `submitted` separately from `accepted`; only independent `work.review` establishes acceptance. Completed/unreported originals use the separately qualified explicit reconciliation tool, preserving its genuine tool name and original history. |
| `cancel` | Existing `work.cancel`/goal cancellation and exact current authority. Retain original uncertain effects until fenced stop/receipts settle. |
| work `handoff` / `yield` | Do not rewrite owner or expire an active attempt. Use qualified rejection/rework/revise flow when applicable; existing `work.recover` is only for exactly reconciled pre-turn denial. General active work transfer needs a separate canonical settled-attempt primitive and remains unavailable. Path-level offer/accept above does not transfer the work. |
| task listing / dependencies | `work.list/read/attempts`, `goal.read`, cooperation and sourced decisions. Accepted prerequisites unblock admission; local `done` or model completion does not. |
| team up / status / member update | `goal.cooperation` with current revisions, explicit executor set and independent reviewer; principal roles and actual delivery/executor state. Room defaults, names, presence and ambient messages confer no admission. |
| requests / sends | Passive updates use ordinary publish; requests address a specific authorized principal and preserve source/automatic terminal reply. Native aliases are display/lookup hints only, never effect credentials. |

Mapped operations retain the original native tool name and call identity in
provenance and record the canonical action separately. Do not rename a native
`coordination_act` call into a fake dynamic tool event. Existing workflow transport
must be extended through the genuine captured callback/effect journal; this stage
does not implement that native port or change the connector.

## Next edit-plan/patch stage: explicit contract, not an available capability

Reserve capability `canonical-edit-plans/v1` and route
`POST collaboration/edit-plans` with the same trusted envelope. A future strict
command union must support `propose, consent, reserve-patch, record-patch,
release, offer-handoff, accept-handoff`; this route is not installed now.
Plans reference existing goal, participant **work IDs**, integrator work ID,
source/mapping revision and physical namespace. They contain named slices with
immutable base digest, byte range or actual hunk digest, intent, ordered handoff
and report-back evidence. Every affected owner explicitly consents by CAS.

Peers may work on independent slice proposals concurrently, including on different
hosts. Shared-checkout writes require one short file mutation permit at a time,
bound to exact current file digest, patch digest, target content digest, plan/claim
revision, current owned delivery and native action. The native preflight reopens
the pinned root/file and validates actual bytes immediately before applying the
patch; patch result receipts bind observed after-bytes and original command.
Multi-file patches must reserve all touched paths in one authority transaction,
with every before/after digest and claim revision; per-file checks are insufficient.
Drift requires re-read/rebase/new consent where scope changes. Free-form slice
text, peer aliases or a matching filename cannot bypass a peer claim.

An unsettled patch command holds its scope even after permit/lease expiry. Reconcile
the original operation and actual bytes; never retry an unknown patch or infer
that stopped execution undid effects. Plans are editing metadata attached to
canonical commitments, not a second mission/work lifecycle. This preserves real
same-file collaboration without requiring permanent separate worktrees.

## Native capability coherence and remaining evidence

Standalone/private mode may keep private SQLite history/claims and original native
tools with explicit local semantics. Enterprise mode must negotiate server,
protected bridge and native runtime protocol/capability revisions together. Local
cache can mirror receipts but cannot become fallback authority during outage.

The server currently advertises only physical claims. Do not turn on the existing
`multi_agent`/`multi_agent_v2` flags merely because that capability exists. Shared
native children need eligible canonical principal/work selection, normal owned
admission, host reservation/attach/liveness, individual context/thread binding,
bounded attributable completion and effect transport. Private sidecars may produce
proposals under the parent's scope; shared commits must pass through the actual
authorized parent/integrator. Never let hidden child threads impersonate its source.

Full restoration requires native RPC and both patch paths, real protected bridge
enrollment, standalone regression evidence and a bounded multi-host/workspace
native demonstration of ownership, slice consent, drift/conflicts, cancellation,
lost replies and independent review. Current PostgreSQL/HTTP tests qualify only
the substrate; their native/controller facts are explicitly synthetic. Global
retains paired review and the source coordinator's independent final review.
