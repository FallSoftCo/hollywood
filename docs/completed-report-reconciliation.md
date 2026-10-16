# Completed original report reconciliation

This operation records evidence after an unreported original assignment completed.
It does not restart the assignment, adopt its claim, create an attempt/context, or
accept work. Ordinary `POST work/{work}/report` remains strict.

## Wire contract

`POST /hollywood/v3/work/{workId}/report-reconcile`, workflow action
`work.report_reconcile`, native tool `hollywood_work_report_reconcile`:

```json
{
  "operationId": "stable-reconciliation-operation",
  "revision": 2,
  "priorDeliveryId": "<exact original delivery UUID>",
  "state": "submitted",
  "effectDisposition": "reviewed",
  "note": "Accountable owner inspected original receipts and effects; specific outcome.",
  "briefingSequence": 123,
  "evidence": [{
    "hostId": "<original host UUID>",
    "workspaceId": "<original workspace UUID>",
    "relativePath": "result.json",
    "sha256": "<64 lowercase hex>",
    "size": 12
  }]
}
```

`state` is `submitted` or `blocked`; `effectDisposition` is `reviewed` or
`unknown`. Both are explicit. Whitespace-only notes and duplicate/noncanonical
relative paths are rejected. Caller claim/generation/context hints are forbidden.
The approved connector hashes the actual original artifacts under its pinned
host/workspace/context boundary; the server verifies those identities and retains
the attestation. The server does not read remote artifact bytes or treat arbitrary
JSON hashes as independently verified filesystem evidence.

The connector supplies its **current** normal participation in the existing
`X-Hollywood-Native-Source` v2 header. The existing transactional authority fence
checks token/principal revocation, exact current claim/generation/turn, role,
placement and independently live current executor. Reconciliation additionally
requires this explicit tool and matching original/current persistent bindings.
The original completed claim is never used as current effect authority.

## State and concurrency rules

Only the current original owner with original-room posting rights and current
cooperation membership may reconcile the latest assigned, completed, unreported,
non-cancelled attempt. Original request, specification, acceptance, result,
delivery, attempt number, context/thread/workspace/host and effects stay unchanged.
An independently observed stopped original executor resource is required; this
does not attest that external effects never happened. Missing legacy persistent
bindings require a separate explicit migration, never inference.

Goal pause permits evidence preservation because it gates admission; this path
admits no execution. Goal/work cancellation, revoked rights, unsettled competing
ownership, source-role/context/host/workspace mismatch and stale CAS fail closed.
Current original-scope decisions must have been read. Conflicted decisions and
unknown original attention/controls remain recorded as unresolved. A blocked
report can retain those facts; a submitted report requires actual artifact
references, a reviewed effect disposition and no such unresolved records.

The authority mutation fence serializes cancellation/revocation and reports.
Goal lock plus work revision CAS prevents two different reports winning. Stable
same-operation retries return the original receipt after rechecking present
rights/ownership/cancellation; changed payload conflicts. Receipt replay does not
revalidate the obsolete supplied revision after the original report committed.
The report event and schema22 `hb_work_report_reconciliation` row are atomic with
work/attempt evidence. Attempt readback includes the reconciliation event/audit.
Accepted native effects of the *original* stay immutable. The current native
participation's normal effect audit records the new report transaction.

Existing independent review remains separate, against the original completed
execution and current evidence/decision sequence. A blocked report cannot be
accepted. Execution completion never establishes accepted business delivery.

## Qualification and activation

`tests/test_completed_report_api.py` checks wire, path and source-envelope
boundaries. Its route-store double is labeled and proves no domain execution.
`tests/integration_completed_report_reconciliation.py` uses a **fresh isolated
RAM PostgreSQL database and real authenticated HTTP**, with explicitly synthetic
native metadata. It checks submission/replay/review, immutable originals, stale
claims/roles/contexts, cancellation, revocation, unknown controls, current
decision conflicts and competing report/report-cancel races. It is not native
acceptance evidence.

Prepare a new harness without starting services/models:

```sh
PYTHONDONTWRITEBYTECODE=1 /opt/harness/production-core-20261016-r03/venv/bin/python \
  tests/completed_report_staged_harness.py
```

With an approved executable **existing** PostgreSQL dependency route, add
`--focused --pg-bindir <existing PostgreSQL bin directory>`. Each invocation
creates a fresh RAM namespace, guards the80GiB reserve plus48MiB phase headroom,
starts only that new database, and stops only its owned process. No fixture
service or production database configuration is reused. Read `focused-status`
and raw command logs; missing dependencies are blockers, not passing skips.

The prepared `harness-plan.json` records the required later real-native journey:
original artifact/effect once without report; a later normal participation in the
**same persistent context** invokes the actual new tool; a distinct authorized
native reviewer inspects and accepts. Preserve exact native/action/request/result
IDs and hashes and verify the effect counter remains1. The native controller/
connector driver is gated on compatible committed LAX code, first independent
cross-review and explicit Global admission. This initial server cut does not
start it or substitute HTTP metadata for the required native proof.

No live activation is authorized here. After independent review and the real
native proof, Global may stage exact paired server/connector packages, back up
the database and retain release/source hashes. Schema22 is additive; preflight
must reject a connector lacking this exact wire contract. Drain/fence only
authorized owners at a verified boundary; unknown effects remain held.

Rollback stops further reconciliation admission and restores the prior paired
runtime at a drained boundary. Retain the additive audit table, new report and
attempt evidence, original receipts and native-effect audit; never roll back the
database/history or replay originals to simulate rollback. An older server may
not read the new audit projection; retain the new exact evidence reader for
inspection and report that compatibility limit.
