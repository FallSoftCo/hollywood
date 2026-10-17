# Agent resource discovery and defaults

Schema 28 adds `canonical-agent-resources/v1` without changing
`canonical-delivery-edits/v1`. Existing explicit delivery editing remains usable
on schema 27. A connector must discover the new capability before using these
additional endpoints. The exact additive contract is in
`agent-resource-wire-r01.json`, with the selection DTO in
`resource-default.schema.json`.

An administrator registers a physical root and separately grants an agent access
using the existing APIs. The new default pointer selects one already authorized
root for an agent and execution host. It has its own CAS revision, so choosing or
clearing a default does not revise access grants. Selection requires current
root and agent-grant revisions, active read/write rights, an active agent and an
enabled placement. Version 1 defaults require the physical root to be local to
the execution host. Remote filesystem compatibility cannot be inferred.

The pointer stores the selected root and grant revisions. A changed registration,
revocation or regrant holds routing until explicit reselection. Participation loss
also appends an invalidation record for that pointer revision; re-enabling a
principal or placement does not revive it. Clearing creates another pointer
revision with no replacement. Revision snapshots and invalidation records have no
update/delete API. Administrative commands retain their original receipts;
replaying an old accepted result does not prove that selection is currently
eligible. Read the current default again before routing.

The default view reports `missing`, `cleared`, `selected` or `held` and always
`mayApply=false`. Stable hold reasons are `participation-invalidated`,
`participation-unavailable`, `resource-unavailable`, `access-unavailable`,
`execution-host-incompatible` and `selection-revisions-changed`. Only an eligible
selection includes current root and access metadata. No default means that
relative intent is held. Never choose the sole or first grant, guess a cwd or
change the private native context.

Administrators can list registered roots, including revoked roots for CAS
discovery, and inspect any same-authority agent's grants. An owner agent can list
its currently readable resources and inspect its own default; visibility filters
run before pagination. Read-only resources remain discoverable but cannot be a
default for editing. Nonadministrator services and other agents cannot use these
owner endpoints. The existing enrolled controller can instead read a default
derived from a canonical delivery's agent and execution host, for either ordinary
or structured requests.

Pages contain at most eight rows (four by default) and use an exclusive workspace
UUID cursor. Each page reauthenticates and reads under the shared authority fence,
so it sees revocations committed by a preceding exclusive writer. Pages are not
a frozen snapshot. Serialized ASCII responses are capped at 32,768 bytes and fail
without truncation if exceeded; a smaller page may be requested. Reads expose no
credential or private instruction bodies. Writes retain existing native-source
and administrative checks, original operation replay and changed-wire conflicts.

Default selection is routing metadata. Actual filesystem root handles,
environment identity, protected callback provenance and current mutation access
still require verification. Planned mutations retain their per-work grants,
claims, exact participant consent and planned permits. The planned target's
`grantRevision` comes from its work grant, never from the default pointer or the
agent access grant. Selection and clearing do not release locks, alter contexts,
create work, enroll bridges or admit execution.

Run only the new mechanism scenario with:

```sh
python tests/canonical_collaboration_harness.py --scenario resource-routing
```

It creates one fresh RAM PostgreSQL instance and local authenticated HTTP server,
then stops the exact owned instance. Source/executor bindings in this scenario
are explicitly synthetic; passing it is not native runtime or model proof.
