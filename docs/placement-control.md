# Placement admission control

Schema 29 adds `canonical-placement-control/v1`. It controls the admission state
of the same existing enrolled placement. The exact interface is in
`placement-control-wire-r01.json` and `placement-state.schema.json`.

An authenticated administrator can inspect any same-authority placement with
`GET placements/{agent}`. An owner agent can inspect its own placement, including
while paused. Nonadministrator services and other agents cannot inspect it.
The response contains the canonical placement, its revision and `enabled` or
`paused` state. `newExecutionAdmitted`, `drainVerified` and `mayApply` are always
false: inspection and state changes do not admit or prove execution.

Use `PUT placements/{agent}` as an administrator with this body:

```json
{
  "operationId": "stable-operation-id",
  "revision": 1,
  "hostId": "00000000-0000-0000-0000-000000000001",
  "enabled": false
}
```

`revision` is the current strict integer revision; `hostId` must match the same
enrolled host. `enabled=false` pauses and `true` resumes. Every accepted new
operation advances the placement revision once, including an explicit same-state
operation. Retrying the exact operation returns its original receipt. A changed
body or endpoint under the same operation conflicts. An old accepted result is
historical evidence: inspect again to establish current state. The existing
administrative command reader provides original receipt recovery.

Writes retain current native provenance for agent administrators. A paused agent
cannot bypass this fence to resume itself. Another currently authorized active
administrator or the existing authenticated human administrator must resume it.
There is no new human ingress or alternative direct-SQL control path.

Pausing serializes with claim/admission and native business checks through the
existing authority fence. Future claims and mutation preflight/begin operations
require an enabled placement, live original source and current access authority.
Pause preserves executors, pending requests, roles, private contexts, placement
generation, attempts, claims, permits and locks. It does not stop an OS process or
withdraw a one-shot apply response already issued. Independently drain the
original controller, bridge and executors before an installed upgrade. Resuming
only changes an admission prerequisite; it does not replay or claim queued work.

The old `POST placements` registration request and response are unchanged.
Existing rows migrate to revision 1 with their prior fields intact. Re-registering
an existing placement preserves its enabled state and revision, so an enrollment
retry cannot silently resume it. Accepted state transitions append before/after
snapshots without an update/delete API, in addition to administrative receipts.

Schema 29 also permits administrator metadata setup for a registered paused
placement. The active subject and exact resource/access checks remain. An active
agent grant can be prepared while paused, and a new explicit default pointer may
pin that grant and local root. The default remains held while paused with
`resource=null`; owner discovery remains filtered by enabled placement. Explicit
resume may make this newly prepared pointer eligible if all current pins match.
An older pointer invalidated by pause stays held until explicit reselection,
which can be performed during pause. No setup write grants apply authority.

The bridge capability reader adds `placementControl=canonical-placement-control/v1`
and `pausedResourceSetup=true`. The versioned resource extension is in
`agent-resource-wire-r02.json`; the r01 file remains immutable. Delivery-edit v1
and resource DTOs, one-use semantics and existing mutation fencing are unchanged.

Run the focused real PostgreSQL/HTTP mechanism scenario with:

```sh
python tests/canonical_collaboration_harness.py --scenario placement-control
```

It exercises supported HTTP pause/resume, offline setup and one synthetic
preflight/begin/unknown lifecycle to verify placement fencing. No patch bytes,
models, native runtimes or protected controller processes are executed. It is
not independent drain or installed/native acceptance.
