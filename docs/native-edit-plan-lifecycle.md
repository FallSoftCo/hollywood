# Canonical edit plans and one-shot mutation permits

Historical r02 contract: r03 [registered physical resource authority](physical-resource-authority.md)
supersedes its context-derived target namespace and requires the v2 bridge profile
and explicit protected target envelope. Consent/permit/settlement semantics below
remain the base lifecycle; protocol events are now audit rather than conversation.

This server implements `canonical-edit-plans/v1` on top of canonical work,
physical path claims, protected source-v2 participation and original command
receipts. It creates no tasks, work admission, native children, reports or business
acceptance. The native handler, LAX bridge and actual patch adapter are owned
elsewhere; the server cannot verify a remote pipe or read remote bytes. Fixture
native/controller/executor facts are synthetic, even when PostgreSQL, HTTP and
owned RAM file operations are real. No production enrollment or deployment is
established by these tests.

## Protected interface

Routes are relative to `/hollywood/v3/`. Exact strict models are in
`hollywood_backbone/edit_plan_models.py`; schemas can be exported with
`EditPlanCall.model_json_schema()` and `MutationObservation.model_json_schema()`.
`EditPlanReply`, `ApplyAdmission` and `EditFailure` define response/error schemas;
the canonical receipt is an extensible domain snapshot.
Unknown fields and boolean revisions are rejected. Metadata is bounded to 16KB
per edit call, eight participants/files and sixteen slices. Individual declared
files and artifacts are limited to 16MiB.

| Route | Contract |
| --- | --- |
| `PUT hosts/{host}/collaboration-bridge` | Existing administrator/CAS command; explicitly enroll the current host controller with protocol `canonical-edit-plans/v1`, reviewed adapter and enrollment hashes. The older `canonical-physical-claims/v1` profile cannot use plans or permits. |
| `GET hosts/{host}/collaboration-bridge` | Current enrolled service or administrator; reports implemented profile-specific `editPlans`, `patchPermits` and port names. `nativeChildAdmission` stays false. Capability does not prove a native adapter is installed. |
| `POST collaboration/edit-plans` | Current protected enrolled service only, envelope below. Participant commands preserve actual `source.tool=collaborative_edit_plan`; preflight/begin/settle preserve actual `source.tool=apply_patch`. |
| `GET collaboration/edit-plans/{plan}` | Authorized room reader or current controller of a participant namespace. Snapshot, consent digests and effective held states only; always `mayApply=false`. |
| `POST collaboration/mutation-observations` | Current protected controller; independently stopped original executor proof and exact all-file observations. Evidence only, never a native callback or apply grant. |
| `GET collaboration/commands/{principal}/{operation}` | Existing receipt lookup, authorized for the original host and event. Returns original wire/digest/acceptedResult without executing it. Service observation commands retain service provenance. |

Edit envelope:

```text
{operationId, bridgeRevision, source, executorId, descriptorDigest,
 serviceBindingDigest, channel:"captured-app-server-rpc/v1", command}
```

The protected channel supplies exact `hollywood-native-source/v2` from the
captured invocation, current controller and original independently observed live
executor. Current role/placement, context, workspace, thread/turn, delivery,
generation, claim, service binding, posting rights, decisions, work ownership,
cooperation and cancellation are checked. Ordinary agent credentials plus identity
fields cannot relay these calls. Remote hashes are protected bridge attestations;
the bridge must compute them from actual bytes, resolve paths safely against its
captured root and verify the source revision. It must not expose a generic
model-callable forwarding or signing interface. Host administration remains in the
trust base.

Every command has `action,planId,planRevision,workId,workRevision,
briefingSequence,note`. Operations are immutable and idempotent per principal:
changing any wire field under an existing operation ID conflicts. New mutation
phases have distinct operation IDs while retaining the exact original patch
source, including tool/call/arguments identity.

| Action | Additional fields and effect |
| --- | --- |
| `propose` | `planRevision=0,manifest`; creates revision1, no consent or mutation authority. |
| `revise` | `manifest`; integrator only, no unsettled permit; appends immutable revision, invalidates all previous consent. |
| `consent` | `consentRevision,manifestSha256,sourceRevision,artifacts,imports,participantConsentDigest,settlementDigest`; nullable digest fields are explicit. Each producer consents to exact artifacts. Integrator consents last to exact peer receipt digest and imports. |
| `revoke` | `consentRevision`; owner revokes its vote. Every reserved/executing permit becomes held and keeps all file locks. |
| `preflight` | `permitId,manifestSha256,consentSetDigest,patchSha256,sourceRevision,filesObserved,leaseSeconds`; all-file reservation after current consent, claims and before bytes. Lease1..30 seconds, capped by earliest file-claim expiry. No apply grant. |
| `begin` | `permitId,permitRevision,patchSha256,sourceRevision,filesObserved`; rechecks all current fences and before bytes. Consumes reservation once. Only original successful response carries transient apply admission. |
| `settle` | `permitId,permitRevision,outcome,patchSha256,sourceRevision,filesObserved`; outcome `applied`, `notApplied` or `unknown`. Known live result must match every declared after/before byte; otherwise preserves evidence and all locks as held. |
| `reconcile` | `permitId,permitRevision,outcome,observationDigest,effectDisposition:"reviewed"`; metadata-only closure of known observed original result after fresh all-party consent. Never executes a patch. |
| `withdraw` | Integrator only, no unsettled permit; closes plan without editing authority. |

## Manifest, namespaces and consent

Manifest fields are `logicalSource,sourceRevision,integratorWorkId,participants,
files,slices,patchSha256`. Logical source is a label, never a physical scope.
Each participant is existing canonical `{workId,workRevision,sourceRevision}` in
the same goal/room with a unique current owner. File entries contain
`relativePath,claimId,claimRevision,beforeSha256,beforeSize,afterSha256,afterSize`.
`absent` requires size0. Source revisions are immutable lowercase40/64-hex IDs;
hashes are SHA256.

Each slice is `{sliceId,workId,relativePath,beforeSha256,byteStart,byteEnd,
hunkSha256,artifactSha256,artifactSize}`. Ranges bind to the exact base file and
cannot overlap, including competing zero-width insertions. Every file and
non-integrator participant needs a slice. The bridge must verify the actual
hunk/artifact mapping and complete patch against the declared manifest; the server
cannot derive patch semantics from opaque hashes.

Physical ownership is `(authority,host,workspace,relativePath)`, derived from
captured participation. Literal canonical POSIX relative paths reject traversal,
drive/backslash/control/non-NFC aliases. Permits require exact active **file**
claims, current original owner/attempt, matching revision/source/content and
unexpired ownership. Directory claims must be explicitly settled/materialized as
file claims before proposing; no directory bypass is implied by plan consent.

Artifact entries are `{sliceId,relativePath,sha256,size}`. Producer artifacts are
scoped by their own captured namespace. For every producer on another host **or
another workspace**, integrator must explicitly import and attest matching bytes
inside its target namespace. Same repository name, revision, workspace UUID on
another host or room never establishes shared files. Same physical namespace
requires the same source revision; foreign producers may use different revisions
but must bind slices to the exact target base. No artifact transport is performed
by the server. Every participant's current bridge epoch and canonical rights are
rechecked at mutation time; cached consent alone is insufficient.

## Atomic reservation, execution and settlement

All plan/claim effects share the canonical authority fence with cancellation and
revocation. Plan snapshots take the authority fence before plan/goal locks.
Preflight checks all target conflicts before inserting any lock, then reserves all
files in one PostgreSQL transaction. An overlapping loser gets no partial lock.
Unsettled locks also prevent claim release, renewal or handoff from invalidating
ownership metadata. Expiry never frees ownership automatically.

Every POST returns `{receipt,admission,replayed}`. Canonical receipts always have
`mayApply=false,newExecutionAdmitted=false`. Only the original successful `begin`
response has non-null admission with `mayApply=true,permitId,permitRevision,
expiresAt,sourceDigest,patchSha256,files,oneShotOriginalResponseOnly=true`. It is
not persisted in a receipt. Every same-operation replay has `admission=null`.
Receipt recovery reconstructs evidence only; generic acceptedResult must never be
interpreted as a grant. A lost reply is unknown and cannot be converted to another
begin or a patch retry.

The protected native adapter must journal before mutation, consume that original
response once in the same captured callback, enforce its deadline/source/patch,
check actual before bytes immediately at both native patch call sites, and retain
the all-file guard through mutation and after-byte observation. It must honor
concurrent stop/revoke controls; the server transaction does not itself make
remote filesystem writes atomic or eliminate the interval after a response. A
partial multi-file filesystem failure is held, never rolled back or retried by
this server. The native `execute_verified_patch`/StepContext/PathUri owner must
implement and qualify those runtime checks separately.

Known `applied` settlement requires every declared after hash/size. Known
`notApplied` requires every before hash/size. Both close this patch unit, advance
explicit claim baselines/revisions, and release locks. Another patch requires a
new manifest revision and fresh consent. They do not accept canonical work.

Unknown outcome, content/source drift, expiry, changed consent, cancellation,
revoked bridge or lost authority holds every file. Read projects expired/invalid
ownership as held even before another write. If the original caller has lost
authority, its settlement is denied and locks remain; a protected independent
observation can preserve later evidence.

Observation fields are `operationId,bridgeRevision,permitId,permitRevision,
executorId,descriptorDigest,serviceBindingDigest,
channel:"stopped-executor-observation/v1",outcome,filesObserved,note`. It requires
the exact original resource and binding, independently observed stopped with a
conclusive registered-unit-drained, registered-invocation-settled or
machine-boot-changed reason. It stores an observation digest and remains held.
It cannot stop/resume an executor, apply bytes or unlock anything.

Later normally admitted participation by the same owners/contexts/namespaces may
consent to that exact `settlementDigest`; every producer must freshly consent and
integrator must approve them last. Reconciliation checks these receipts, current
canonical rights and known observed bytes, then closes the original permit. It
does not replay original source or manufacture a native callback. Unknown bytes
stay held. Current cancelled participation cannot authorize cleanup; a later
ordinary request is required and is not created by this protocol.

## Errors and qualification

Malformed strict input returns400 `invalid_edit_request`. Current protected
authority failures inside the edit handler return403 `authority_denied`. Initial
HTTP authentication uses the existing generic403 boundary response. Domain
conflicts return409 with
`{error,message,mayApply:false,retry:"read-original-operation-or-reconcile"}`.
Codes include `invalid_manifest`, `slice_conflict`, `scope_mismatch`,
`work_changed`, `claim_changed`, `claim_expired`, `consent_required`,
`consent_changed`, `participant_unsettled`, `file_set_mismatch`, `content_drift`,
`stale_revision`, `observation_changed`, `plan_closed`, `artifact_mismatch`,
`permit_held`, `scope_busy`, `source_changed`, `patch_changed`, `permit_expired`,
`unknown_effect`, `executor_unsettled`, `capability_missing` and
`operation_changed`. No error or5xx licenses application. Read original operation
and reconcile observed evidence; never use a new operation ID to repeat uncertain
mutation.

Inherited generic conflicts use409 `source_or_scope_changed` with
`mayApply=false`. Optional message/retry fields are absent on generic conflicts,
authority denials and input errors. Invalid read route IDs/query fields use the
existing generic400 boundary response. These transport failures also grant no
permission.

`tests/canonical_collaboration_harness.py --scenario edit-plans` runs fresh RAM
PostgreSQL, authenticated HTTP, atomic races and actual owned file hashes/writes.
It independently stops its exact PostgreSQL incarnation and retains raw records.
The fixtures cover consent, cross-root imports, one-shot admission, known/unknown
results, drift, expiry, revocation, recovery and preserved canonical work lineage.
They do not establish real native bridge/channel or patch-adapter proof.
