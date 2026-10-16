The native proof driver is prepared for source review. Native execution is held.
This preparation does not qualify native integration or authorize activation.
It preserves the qualified Hollywood domain cut `831adf9993044d325710b80e07d891bbd7ea6a67`
and selects LAX `31759cede2bff54c46a2047efeaf7aab5b1990ae`.

From the owned Hollywood worktree, the preparation-only command is:

```sh
PYTHONDONTWRITEBYTECODE=1 /opt/harness/production-core-20261016-r03/venv/bin/python tests/completed_report_native_driver.py prepare
```

It creates a fresh RAM directory under `artifacts-hollywood/driver-preparation-r04`,
including a hashed input plan, fresh fixture-only credentials (mode0600), finite
unit definitions, connector configuration templates and three phase prompts.
It creates no PostgreSQL socket/data, server, controller, native process or work
admission. `check-runtime --plan PATH` exits held when separate admission is absent.
Preparation and unit tests never call `run`.

The future runner contains the actual PostgreSQL/application/controller and
finite-unit orchestration. It starts PostgreSQL directly under an actual nonroot
namespace account, retaining its original PID/start token. It uses the exact
staged server application and LAX source with existing binaries/share/libraries.
Host enrollment uses `lax_connector.host --enroll`; executor reservation, native
attach, registration and independent live/stopped observations remain entirely
in the genuine connector/controller paths. There are no SQL inserts or updates
to delivery, native binding, executor, transition, activity or effect tables.
Installer bootstrap creates only the clearly labeled synthetic authority,
principals/credentials, room, membership, placement and goal-source metadata.
The synthetic goal-source event deliberately has no delivery. Work creation and
the one original assignment then use ordinary authenticated APIs.

There are exactly three finite native admissions in one proof: the original
worker writes an exclusive counter/artifact and completes unreported; later
ordinary participation by that worker invokes the actual reconciliation tool;
the distinct fixture Global manager inspects the bytes and invokes ordinary
review. Each unit has `Restart=no` and a distinct explicit claim operation. The
driver waits on kernel registration/readiness events and owned child completion;
it never polls or restarts a model. It checks original request/result, delivery,
context, binding, native activity/effects, immutable attempt fields and assignment
fields byte-for-byte through retained JSON snapshots. Artifact bytes/hash and
counter remain unchanged, with exactly one attempt and three deliveries. Both
tool actions require actual native-source-v2 wire provenance and started/completed
dynamic activity. Completed execution alone cannot satisfy either report or review.

Every launch retains logs and PID/start tokens. A registered unit's exact
InvocationID, guardian PID/start and cgroup fence cleanup. Changed or unregistered
incarnations remain held; the driver does not invent ownership to stop them.
Finally cleanup stops the owned units, controller, application and PostgreSQL,
checks drainage and preserves all evidence. `proof/result.json` is written only
after both assertions and cleanup succeed. An exclusive admission marker prevents
rerunning an interrupted proof; inspect the original operations instead.

The missing capability is a reviewed managed namespace, not another HTTP fixture.
`service_observer.expected_owner` and host enrollment hardcode protected
`/var/lib/harness` records, actual NSS homes, root-owned installed systemd units,
actual `/proc` incarnations and cgroups. The namespace must provide private
mount/network/PID namespaces, a distinct private system bus and delegated cgroups,
RAM-backed registry/NSS/unit/runtime state, the two actual accounts and genuine
protected provisioning records for the generated IDs. Installed units must be
inactive before proof admission. Existing sources/binaries must be readable in
place by the actual accounts. All native homes/history/state must be fresh RAM;
the reviewer needs an actual read-only route to original artifact bytes.

The unchanged `linux_worker.provision` copies `auth.json` and enables a resident
service. Calling it unchanged would violate this contract. No suitable approved
namespace/provisioning route or provider mount is currently supplied. A plain
subprocess, borrowed production service, hand-written executor rows, patched
observer, or claim in a capability JSON cannot replace these facts. No namespace,
account, service or credential changes were made during preparation.

Before execution, Global must review both final component reviews, this exact
driver and the actual namespace/provider setup. A root-protected
`completed-report-native-proof-admission/v1` receipt must identify the sourced
Global admission, operation, plan SHA256, driver SHA256 and maximum three native
admissions. A separate root-protected `completed-report-managed-namespace/v1`
receipt must bind that plan and IDs, the actual pgUser, namespaceBackingRoot,
providerApprovalSource, zero setup model starts, credentialsCopied=false,
freshNativeHistory=true and reviewerArtifactAccess. These receipts are necessary
review records; the driver also checks actual namespace/socket/path/account/unit/
source facts. Preparation does not create either receipt. Only inside that
reviewed namespace can the separately authorized command be used:

```sh
python tests/completed_report_native_driver.py run --plan PLAN --admission GLOBAL_RECEIPT --namespace-capability NAMESPACE_RECEIPT
```

The namespace launcher/provisioning capability is outside this prepared driver
and remains an explicit blocker. The runtime branch has not been executed;
preparation tests validate generation, gates and evidence predicates only.
Deployment/rollback remains separately gated: preserve the current installation
until native proof and Global acceptance; any later release must pin the exact
reviewed component cuts and retain its original rollback manifest.
