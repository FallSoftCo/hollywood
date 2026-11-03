# Independently maintained Hollywood API code

The API package is not a native model/profile/connector upgrade. Keep the
original SDK/capsule, installer, manifest, launch acceptance and rollback bytes
immutable. Do not rerun a first-enrollment initializer to change an API pin.
`python -m hollywood_backbone.api_component` provides an additive, owner-local
API-component successor operation; it does not enroll, reload or restart.

1. Fetch an exact published code artifact through the existing enrolled Fleet
   identity reader. Verify its strict artifact manifest, size/hash and source
   authority. A Hollywood credential is not implicitly a Fleet read credential.
2. Qualify the new `.pyz` offline under the original service interpreter and
   dependencies, without live config/auth reads or a second server. Retain the
   original package, launch definition/hash and current process identities.
3. Write a reviewed `hollywood-api-component/v1` spec: component name, kind
   (`systemd` or `launchd`), exact `definitionFile`/`definitionSha256`, `from`
   launcher/path/hash, and `to` launcher/path/hash plus package/path/hash. Each
   launcher hash is named `launcherSha256`; package hash is `packageSha256`.
4. Run `prepare --spec SPEC --operation NEW_PRIVATE_DIRECTORY`. It validates
   the pinned original and successor code and creates original/successor
   definitions plus an immutable operation plan. Only one program argument is
   changed. Inspect/review these files and the exact diff before cutover.
5. At an owning quiet boundary, inspect reverse lifecycle dependencies and
   current native/Global/database/gateway process incarnation evidence. Preserve
   deliberate stops and existing enabled/restart/principal policies. `commit`
   only writes the reviewed definition; `inspect` is side-effect-free. Neither
   function claims manager reload, service activation or health.
6. The local owner explicitly performs the necessary standard manager
   definition reload and API-only restart under a SEPARATE durable activation
   intent. After uncertainty, inspect that original operation/current process;
   never blindly repeat restart or enrollment. Verify actual new PID argv,
   package hash, health and own authenticated room read, and unchanged protected
   process/context identities before writing activation acceptance.

Rollback is the same explicit owner operation using the retained original code
and definition, not a rewrite of original installation receipts. A changed
definition or package holds the original operation. A repeated successful
definition commit reconciles byte-identical current state without another write.
An interrupted write whose current definition is back at the original stays
held because it may have been deliberately rolled back.

POSIX definition commits hold a standard nonblocking per-component kernel file
lock across inspect, original intent, compare and replacement. A competing
operation holds without creating another write intent. This fences cooperating
maintenance callers; it is not a claim of protection against a hostile root.
The focused real two-process barrier case pauses one commit at replacement and
proves the second cannot enter, then rejects its stale definition after release.

Windows uses the separate owner-local
`maintenance/windows-api-component/api_component_windows.py` entrypoint, with
the exact original limited Session1 owner, accepted task/ACL/normalizer and LAX
private-file primitives. See its adjacent `API_COMPONENTS_WINDOWS.md`; do not
route Windows through the POSIX definition writer. Its locked append-only
registration/predecessor chain supports separately pinned API-only successors
and rollback, preserving the original installation forever. Neither prepare nor
registration starts/enables/stops a task or claims API health.

The exact BLD r02 adapter bytes were integrated from source ZIP SHA256
`aadad924387f502f898789c0320e275d366f03bde007716ad4029d6994aa9e05`.
Actual Windows functional registration/lost-ACK/rollback checks passed, but that
frozen driver's terminal `IRunningTask.Refresh` returned an error after its
finite child exited. A separate cleanup-only continuation verified terminal,
zero instances and deletion without replay. This is assisted qualification,
NOT an unqualified automated exit0 or live product activation. Retain the
original `qualification-r02/result.json` SHA256
`7f992703d115db1a58d071beea64f501424e799e94b759d242f8214a6756ac20`.
Frozen-driver repair/native rerun and real API offline/drain/activation/read/
rollback gates remain explicit. Never overwrite hardlinked capsule files or
modify the Global/native task as an API update.
