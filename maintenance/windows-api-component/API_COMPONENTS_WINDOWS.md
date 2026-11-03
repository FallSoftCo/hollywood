# Windows API component successor operations

Maintained integration r03: production adapter bytes remain EXACT BLD r02 SHA256
`3ef593f4255abcff58536e11d3594fce83e11d4cd335dbb05b64441a1eb82565`.
The driver now uses a fresh `qualification-r03`, pins the intervening r02
assisted-cleanup result as well as r01 cleanup/current task absence, and checks
that all five dated protected process identities are actually present. It does
not refresh a stale `IRunningTask` after pinned child-handle exit0; it separately
checks current task-instance absence. Non-Windows collection skips this explicit
platform journey. These are driver/integration corrections, not another COM
registration policy. A new actual frozen-byte Windows run is required before
claiming r03 automation passes; the original r02 assisted limits remain intact.

This additive owner-local adapter registers a separately pinned Hollywood API
launcher in the **existing dedicated task**. It does not install/enroll a host,
start/enable/stop a task, drain a server, alter a profile, or accept service
health. The original capsule, task spec/install/XML/start receipts, dependencies,
private configuration and application state remain untouched.

Source authority: the supplied portable r03 tools are based on Hollywood
`6855ed854f34ec72f9b4f5de3440c8f1bc3011e0`. This Windows implementation is a
new local source snapshot, not a Git commit. Only `api_component_windows.py`,
`test_api_component_windows.py` and this document are owned here. Generic
`api_component.py` and server/lifecycle source remain CPU-owned.

## Environment and original acceptance

Run through the existing Windows capsule boot under its existing Python3.12,
pywin32 and LAX private-file helpers, as the original limited Session1 owner.
No additional dependencies, elevated token or another account is supported.
The accepted semantic normalizer is the retained `windows-host-tasks-r03.py`
with SHA256 `10c274e823fdd86ab50a0f800952532c543e9c1d1e65f68c6e0f4a037a8f9b0a`.
It is loaded for `definition_identity` only; `install`/`start_once` are never
called. The production task is exactly `LaxHost-BLD-11a7ef1b-Hollywood-r01`.
One explicitly named disposable QA task is also allowed by the adapter.

Prepare is a read of Task Scheduler plus writes of NEW protected operation and
component-lock evidence. Inspect reads the original plan/code and current
scheduler definition under the component lock. Neither dispatches manager
registration, activation or configuration changes.

## Reviewed component specification

Use a private JSON file with schema `hollywood-api-component-windows/v1` and:

| Field | Binding |
| --- | --- |
| `component` | The original dedicated task name. |
| `normalizer` | Exact retained accepted source path; fixed source SHA checked. |
| `originalSpecFile`, `originalSpecSha256` | Original immutable `lax-windows-task/v1` file and its canonical JSON digest, matching original installation acceptance. |
| `installationFile`, `installationSha256` | Original installation receipt, its raw-byte digest and matching task binding; receipt parent must be the original record directory. |
| `interpreterSha256` | Exact original Pythonw binary. Its path/flags/cwd are preserved from the accepted task spec. |
| `liveXmlSha256`, `taskAclSha256` | Reviewed current registered XML and actual owner/group/DACL string digests. Do not substitute stale XML for a fresh review. |
| `from.launcher`, `from.launcherSha256` | First operation: exact original launcher argument/hash. Later operations: exact accepted predecessor successor launcher/hash. It must occur exactly once in the derived accepted command. |
| `from.package`, `from.packageSha256` | Original API package and its retained hash. |
| `to.launcher`, `to.launcherSha256` | Reviewed successor launcher in a separate private revision outside the original launcher/capsule directory. |
| `to.package`, `to.packageSha256` | Verified independent successor archive; safe bounded ZIP/CRC checked without extraction. |
| `dependencyPins` | 1–32 exact path/hash entries, including the accepted capsule/dependency manifest and other owner-reviewed dependency/boot pins. |
| `predecessor` | `null` for original-to-first transition; otherwise exact accepted operation path, raw `planSha256`, and raw `acceptanceSha256`. |

Successor files must be private, regular, exact unaliased paths with one hardlink.
Do not overwrite the sealed original archive or a shared hardlink. The owner
must review the successor launcher's imports/package binding against these pins
and qualify offline imports under the original dependencies. The adapter pins
code; it cannot infer the intended behavior of arbitrary Python code from its
filename. All other original command arguments, including config/state paths,
remain exact. No credential/profile/database content belongs in the spec.

Subsequent updates/rollback retain the ORIGINAL installation/spec permanently.
Their predecessor must be an accepted format2 operation under the SAME derived
component lock/ledger, with matching original interpreter/dependency/acceptance
bindings. The adapter verifies that predecessor's plan, registration intent,
acceptance, ledger, retained code and successor XML, recursively derives its
accepted command, and compares the actual current XML/ACL/enabled/task policy
against that exact accepted successor. No arbitrary current-task adoption is
allowed. Unknown, stale, missing, cyclic/deep or unaccepted predecessor chains
hold. A dependency/profile/interpreter migration is not an API-code-only chain.

Prepare with the existing boot and `--script api_component_windows.py prepare
--spec SPEC --operation NEW_ABSOLUTE_PRIVATE_REVISION`. The existing normal
Python executable should be used with `-I -S -B -X utf8` before the boot path.
CLI output contains state and digests, not task ACL/account values.

Prepare retains registered original XML and its COM-exported definition XML,
the successor XML, normalized definitions, actual SDDL, enabled policy and the
reviewed code/spec pins. COM may elide explicit defaults, reorder named XML
properties and embed the supplied SDDL during registration. Comparison uses
COM-exported definitions, a limited known-default/property canonicalization,
the accepted semantic normalizer, and separate **exact actual SDDL** comparison.
Action and trigger list order is preserved. Unknown/nondefault settings are
not discarded. A nondefault Priority change is independently rejected.

## Registration and uncertain results

Commit requires a separately reviewed private quiet-boundary JSON and its hash:

```json
{
  "schema": "hollywood-api-quiet-boundary/v1",
  "approved": true,
  "component": "LaxHost-BLD-11a7ef1b-Hollywood-r01",
  "operation": "EXACT_ABSOLUTE_PREPARED_OPERATION",
  "originalXmlSha256": "FRESH_REVIEWED_ORIGINAL_XML_DIGEST",
  "planSha256": "RAW_IMMUTABLE_PLAN_JSON_DIGEST",
  "successorXmlSha256": "EXACT_PREPARED_SUCCESSOR_XML_DIGEST",
  "approvedAt": 0
}
```

The timestamp must be current within 300 seconds. This receipt documents an
actual owner decision; it does not grant authority or substitute for real
graceful draining/reverse-dependency and rollback coverage. The task must be
stopped (Ready or Disabled), with zero instances, both before the durable intent
and at the final comparison. A running API cannot be updated by this adapter.
Both sealed plan and successor XML hashes are required and checked before any
component claim, registration intent or manager dispatch. The operation path
alone is not a review of the target bytes.

Use `commit --operation ORIGINAL --quiet-boundary RECEIPT --quiet-sha256 SHA`.
A standard nonblocking Windows byte-range lock is derived from the ORIGINAL
accepted record directory, not chosen independently for each operation. It
covers current inspection, durable component claim/original registration intent,
final comparison, COM update and readback/append-only acceptance. The Windows
lock byte is checked by ACL/type/size without reading its locked contents;
Windows byte locks also prohibit another process's reads.

A per-component append-only registration ledger holds DIFFERENT operations
while an original dispatch is unresolved. A crash between component claim and
operation intent fails closed. Do not delete an intent/claim to permit retry.
Task registration uses UPDATE only, no automatic principal ACE, and ignores
registration triggers; original task SDDL and InteractiveToken principal remain
exact. It neither enables nor starts the task. Independent uncooperative owner
or OS-trigger activity is not fenced by this cooperative lock; any changed
readback is held. Live owner quiet/trigger coverage is still required.

After a lost acknowledgment, inspect/reconcile **that original operation**.
If the exact successor definition/ACL/policy is present, acceptance can be
appended without another COM registration. If the task is original, rolled
back, missing or unexpected, hold; never repeat registration. Later successful
work does not prove an earlier unknown effect. An accepted repeat is readback
only. Original acceptance and ledger files are append-only.

## Activation, rollback and live limits

Registration acceptance is not activation or health. The existing owner must
separately establish graceful API drain and quiet boundary, record an API-only
activation intent, perform a single explicit authorized start, and verify the
actual loaded package/PID, health and authenticated room read. Preserve native,
Global, gateway, NATS, DB and all thread/profile/auth identities. Do not invoke
the first-enrollment installer or infer permission to enable a deliberate stop.

Rollback needs its own reviewed successor operation to the retained original
code and definition under the same lock/ledger and owner quiet coverage. It is
not a rewrite of original installation history or an automatic reaction to a
timeout. Use a verified independent private copy if retained rollback code is
hardlinked inside a sealed capsule; do not overwrite/unlink shared originals.
The frozen fixture exercises accepted successor-to-rollback pin registration;
real API reverse activation/health remains a live-release qualification gate.

## Frozen-byte qualification and retained earlier evidence

The earlier `windows-api-component-39f94f8f-r01/qualification-r01/` remains
immutable: all initial fixture failures, exact original-operation recovery,
serializer/lock diagnoses, actual registration journey and final-source focused
checks are retained. Its terminal-process and deleted-QA-task acceptance is
checked, and the exact task must independently be absent before this new
revision creates a clearly recorded disposable task. Earlier failures are not
hidden or recounted as this revision's green checks.

This revision's fixture freezes all THREE final source hashes in
`qualification-r02/frozen-source.json` BEFORE any QA task registration. It
refuses an existing qualification directory; there is no resume/force/retry
mode. On failure retain the exact task/intent/current receipt, do not create a
replacement. The following is the fixture contract, not a claim of success
without its actual `result.json`:

1. Prepare and inspect without manager effects. Mismatched raw plan and
   successor-XML quiet receipts must each hold with zero registration intent
   and unchanged live XML.
2. Actual update on the disabled, trigger-free QA task; a second process must
   be denied by the held kernel component lock. Inject a lost acknowledgment
   only AFTER the real COM update and readback. Unknown predecessor preparation
   and a competing operation stay held. Reconcile/repeat the exact first
   operation without another registration.
3. Reject a stale predecessor plan digest. Prepare an accepted successor-to-
   rollback operation on the SAME task by deriving the current accepted command,
   and register/read back the independently pinned rollback revision. Preserve
   original installation/spec forever; disabled state and exact ACL remain.
   Original update replay after rollback must hold.
4. Separately explicitly enable/start ONLY QA once. Its finite harmless Python
   action reads the rollback fixture package, writes metadata and uses a
   two-event handshake so the controller can pin and await that exact process.
   After terminal/zero-instance proof, disable/delete only QA and verify absence.
5. Verify all frozen source hashes and product task/spec/ACL/file/process
   identities unchanged, and report the exact disk closure/budget/limits.

Actual successful acceptance is `qualification-r02/result.json`, plus
`chain-journey.json`, `quiet-negatives.json`, kernel barrier, registration/start
intents and exact cleanup acceptance. Read their contents and hashes; presence
of this source or a frozen-source file alone is not a passed journey. No new
native daemon, worker, model, app server or global dependency is in this test.
No real API package activation is authorized by its result.

Budget was 4MiB, disk-backed. Observed source/evidence bytes are recorded; this
small source exercise does not claim a continuously sampled storage peak or
invent a BLD numeric storage floor. Root retrieves the credential-free source
ZIP through its existing operator path; the artifact-reader role is read-only,
not upload authority.
