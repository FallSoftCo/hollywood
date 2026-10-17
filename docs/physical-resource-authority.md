# Registered physical targets and protocol projection

The r03 server correction requires `canonical-edit-plans/v2` for all claim,
plan/permit and stopped-observation effects. `hollywood-native-source/v2` is
unchanged: it identifies the actual agent, private persistent context/workspace,
source host, native callback and executor. A separate registered resource names
the authorized edited checkout. Multiple agents with different private contexts
use one resource ID for the same checkout. One agent can have grants to multiple
resources without changing its context. A physical execution environment may be
routed to another registered host; source host and resource host remain distinct.

## Canonical registration and grants

All routes below are relative to `/hollywood/v3/`. Strict schemas are in
`physical_models.py`, `collaboration_models.py` and `edit_plan_models.py`.

| Route | Request and authority |
| --- | --- |
| `PUT hosts/{host}/physical-workspaces/{workspace}` | Existing administrator authorization, native source if administrator is an agent, original administrative operation receipt and CAS. Body: `operationId,revision,state,rootPath,environmentSha256,rootIdentitySha256,registrationSha256`. Host is the registered physical host. |
| `PUT physical-workspaces/{workspace}/grants/{work}` | Same administrator/receipt/CAS fence. Body: `operationId,revision,workspaceRevision,workRevision,state`. Owner, persistent context and execution host are derived from current canonical assigned work/context, never supplied by the model. |
| `GET collaboration/physical-targets/{work}` | Current enrolled source-host controller for the current work owner; checks current room read rights. Returns registered descriptors/grants with `mayApply=false`. Inactive/stale descriptors remain visible for evidence and cannot authorize effects. |
| `PUT hosts/{host}/collaboration-bridge` | Explicitly select `canonical-edit-plans/v2`. Old profiles remain readable but cannot issue target effects. Capability reports `physicalTargets=true,targetSchema=hollywood-physical-target/v1,legacyImplicitContextMutations=false`. Child admission remains false. |

The administrator records a reviewed root registration, not a free-form alias.
Physical host, canonical absolute root path, environment and root identity are
immutable under a resource ID. A new revision can revoke/reactivate that identity
and updates the registration receipt. Duplicate physical root identity on a host
and equal/nested paths within one environment are rejected, including retired
registrations. Existing ownership is never moved to a new ID. The same physical
checkout reached through another URI/environment must resolve to its existing
registered resource, with an explicit reviewed mapping; a new name is not a new
namespace. Distinct roots/hosts have distinct resource identities even when their
relative paths coincide. The registry is not a global filesystem path lookup.

`environmentSha256` binds the reviewed PathUri/environment/executor routing
descriptor. `rootIdentitySha256` binds the actual stable physical backing-root
identity/handle attested by that environment's trusted adapter. Its identity must
remain equal across aliases to the same backing checkout and change when the
underlying root is replaced. A descriptor digest or administrator token alone
does not establish those filesystem facts. Registration and per-action resolution
must be qualified with the actual protected adapter before runtime use.

Grants are per existing canonical work, owner, stable context and execution host.
They do not assign work or permit a model to select arbitrary roots. Current work
revision, source/role/placement, posting/cooperation/decision rights, root/grant
revision/state, bridge enrollment and actual executor are checked for effects.
Updating a grant or root invalidates old consent/permits. Revocation and expiry
retain ownership; no claim, permit or work is reopened by changing registry rows.

## Protected per-action target

Every claim, edit-plan and mutation-observation envelope requires a top-level
`target`. Model command schemas still reject resource/host/context overrides.

```text
target = {
  schema: "hollywood-physical-target/v1",
  workspaceId, workspaceRevision, grantRevision,
  environmentSha256, rootIdentitySha256,
  resolution: "beneath-no-symlinks/v1",
  actionDigest,
  paths: [{relativePath, resolvedPath}]
}
```

`actionDigest` is the existing canonical SHA256 `Store.digest` of the complete
envelope **excluding target**. This includes operation ID, exact source, executor,
descriptor/service binding, channel and command (or original observation fields).
Canonical JSON uses sorted keys, compact separators and the existing Unicode
encoding rules in `store.py`. It binds the protected target resolution to this
specific callback and operation; it is not a secret or a signature. Authorization
still requires the captured channel and separately enrolled current controller,
current original source and independently observed executor/service binding.
Agent bearer credentials plus these same fields remain insufficient.

The protected native PathUri/environment/executor adapter must resolve the actual
target against the registered root handle for **each action**, verify current root
identity and environment, and enforce no symlink or alias traversal beneath that
root. Existing components must be opened/resolved safely; file creation must
verify its parent handles, and rename/delete/multi-file patches must include every
actual affected path. Resolution and before/after-byte checks must remain guarded
through native mutation to prevent a later filesystem race. `resolvedPath` must
exactly equal the registered environment's canonical root plus the literal
relative path. The server checks this relation and the exact path set, but cannot
read a remote filesystem or protect against a compromised host/controller. No
global server path assumption or generic model-callable signing/forwarding RPC
is allowed.

| Action family | Exact target path set |
| --- | --- |
| Claim acquire / other claim commands | New relative path / original claim path. Explicit handoff also checks a current grant for the recipient to the same resource; acceptance uses its own captured target attestation. |
| Plan propose/revise, preflight/begin/settle/reconcile/withdraw | Every manifest file, or the exact stored manifest revision's files. No partial target sets. |
| Consent / revoke | Distinct artifact/import paths / paths from the exact current vote. Foreign-resource artifacts require explicit matching imports in the integrator resource. |
| Stopped observation | Every file from the original permit's manifest. Exact original executor/descriptor/service binding and independently stopped evidence remain mandatory. |

Claims/plans/permits store physical `(authority,resourceHost,workspaceId,path)`
identity. Consent separately stores the source's private workspace and its target
attestation. Current source validation never substitutes resource identity into
the private execution binding. Participant source fences use the original source
host/context; target fences use the registered resource and grant. Original patch
source, short lease, patch/before/after hashes, atomic all-file reservation,
one-shot original begin response and held-result reconciliation retain the r02
semantics. No receipt or registration grants replay authority.

Legacy unregistered claims/consents/permits are not converted on migration.
Unreleased claims without a registered target hold new target effects across the
authority until explicit reviewed cleanup. If a reviewed registration identifies
an old claim namespace, its same owner may inspect/release it under current
registered scope and existing settlement/control fences; this never silently
rewrites its original receipt or maps an unknown external checkout. Otherwise
leave it held for a separately reviewed reconciliation. Existing command receipt
GET remains evidence-only. No live registration/migration was performed here.

## Conversation and native action details

New operational events have `data.type=collaborationProtocol`, their immutable
canonical receipt, original actor and source, and an exact native call/operation
reference. Stopped observations are service-authored evidence and do not invent a
native call. Events inherit the original request audience. They remain in audit
(`view=all`), command receipts and native-effect ledgers as appropriate.
The all-events read projection explicitly marks these as `protocolAudit`,
`conversation=false,actionCard=false`, with original operation/source links when
available. This read metadata does not change the stored event or receipt.

Conversation SQL excludes both new protocol events and legacy progress events
with `collaborationReceipt`/`editPlanReceipt` before pagination. It never deletes
or rewrites those historical records. Intentional messages remain conversation.
`view=activity` still contains only actual supplied native activity records.
No protocol command creates an action card, lifecycle phase or completion.

On read, an existing native action receives `protocolReceipts` only for exact
same actor/room/delivery/generation/thread/turn/call ID (`itemId`), with existing
audience filtering. Links include operation, original source, receipt and audit
event ID; every link is `receiptOnly=true`. Without a matching native item,
evidence stays in audit and original command lookup. No synthetic item is made to
fill a missing native lifecycle record. The real adapter must preserve this
actual call/item identity in its native activity journey.

## Qualification and remaining gate

Fresh RAM PostgreSQL/authenticated HTTP tests use actual owned root/file hashes,
separate private contexts and physical namespaces, resolver rejection of an actual
symlink, explicit grants, shared overlap/handoff, multiple/routed roots, revocation,
all-file races, retained consent/one-shot/unknown holds, and unchanged audit events
with intentional conversation versus native activity details. All controller,
executor, channel and native-item facts in these fixtures are **SYNTHETIC**.

A real two-agent journey with separate private contexts targeting one disposable
registered checkout, actual PathUri/environment resolution, both native patch
paths and genuine action projection remains required. Root performs independent
review; LAX owns native/connector integration. No native/model/controller start,
deployment, private-context rebinding or independent candidate acceptance is
established by this server cut.

## Assigned work and file coordination

The canonical-work-delivery-edits/v1 capability permits a captured original Work execution to use the existing delivery edit protocol when its target files have no explicit collaboration owner or ready plan. Work allocation does not itself require a file plan. The server derives and pins the current goal, assignment, attempt, role/context, decision, placement and membership revisions in permit.proof.workAuthority. Subsequent phase and original-operation reads recheck that authority. Resource grants remain independent, and claims, ready plans and unsettled permits share the existing physical namespace, including conflicts from the same agent. A newer watermark, restored access or resumed goal cannot revive a changed original authority. Ordinary requests retain null Work authority. These metadata checks do not stop an already executing OS command or establish transactionality of filesystem effects.
