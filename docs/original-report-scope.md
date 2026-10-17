# Original-delivery scope evidence for report recovery

`completed-report-scope/v1` adds an explicit original-work read to later report
reconciliation. A later room-B global sequence cannot substitute for original
room-A scope evidence. Source-v2 private identity, the current active request and
native executor remain unchanged while reading the original scope. The server
does not resume original execution, hash remote artifacts or accept work here.

## Versioned interface

All routes are relative to `/hollywood/v3/`. Strict types are defined in
`report_scope_models.py`; the reconciliation request remains
`server.WorkReportReconcile` with required `scopeRead`.

| Route | Contract |
| --- | --- |
| `POST work/{work}/report-briefing` | Current authenticated source-v2 participation with actual tool `hollywood_briefing`. Body `schema:"completed-report-scope-read/v1",operationId,revision,priorDeliveryId,readId:null,cursor:0,pageSize:1..32`. `revision` is the current original work revision. |
| Same route, subsequent page | Same work/prior/revision and current participation, returned `readId` and `nextCursor`, a new operation ID. Page size remains bounded. Every page is explicit; the server does not assert that hidden automatic fetching reached a model. |
| `POST work/{work}/report-reconcile` | Existing later-current tool `hollywood_work_report_reconcile`, existing report/effect/evidence fields and mandatory exact `scopeRead` from the completed read. A bare `briefingSequence` is insufficient, including for an empty decision scope. |
| `GET commands/{operation}` | Existing owner-authorized canonical command receipt. Recovers original page/report wire and acceptance evidence without replaying original work or creating a fresh scope proof. |

`ReportScopeBriefing` returns `schema:"completed-report-briefing/v1"`, the server
read ID/revision, exact canonical original `scope`, the first page's actual
authenticated reader source, declared decision scopes, attributed decision page,
snapshot/digest/sequence fields, issued/expiry times, total and covered counts,
`complete`, `nextCursor` and `scopeRead`. Before the final page `scopeRead=null`.
An empty relevant decision set completes explicitly on its first response.

The final proof is:

```text
{schema:"completed-report-scope-proof/v1", readId, revision,
 snapshotSha256, scopeSha256, decisionsSha256, complete:true}
```

It is a reference to protected server evidence, not a caller-generated hash or a
bearer authorization token. The server compares the exact proof to its own row,
coverage and scope, and checks its authenticated owner/current participation.
Providing a known read ID with forged completeness, different digests or a foreign
owner/assignment cannot satisfy verification.

The original scope binds authority, current original owner, work/revision,
original goal/source/direction, work room, goal room, original request and issuer
identity/kind, delivery/generation/claim, host/private workspace/context,
thread/turn/result, immutable attempt number/specification/acceptance and
cooperation revision. These fields come from current canonical rows and the
preserved original attempt/result lineage; model arguments cannot replace them.

Relevant decisions cover room-wide and original-goal decisions in the original
work room and the original goal room when distinct. Those exact scopes are
declared in the response and require present read access. Other rooms' decisions
are excluded. Every current decision revision, including retracted/conflicted
state, body, issuer/kind, source event, event ID and sequence is included in the
snapshot. Ordering is deterministic. Snapshot capacity is bounded to4096 current
decisions; exceeding it holds with `scope_too_large`, never a truncated proof.

The original work-room sequence is separately recorded. Global `asOfSequence` is
a diagnostic watermark. The three digests bind exact original scope, complete
decision list and full snapshot (scope, first reader, declared scopes, decisions
and watermarks). They use the existing `Store.digest` canonical JSON algorithm.

## Issuance, paging and current fences

Read sessions are additive `hb_report_scope_read` evidence, not task authority.
Each page uses the existing immutable `hb_command` receipt and native-effect audit
with its original current call/source. Page coverage advances atomically under
the canonical authority/goal/decision fences. A replay of the exact operation,
source and body returns its original response without advancing coverage. Skips,
reordered cursors or changing an original operation conflict. After completion,
recover the original final-page operation or start a new explicit read; another
operation cannot extend the completed session.

The server records page response issuance at commit. `coveredCount`/`complete`
mean every response covering the snapshot was issued in order. This does **not**
acknowledge network delivery, connector output, model receipt or comprehension.
A lost response must be recovered from its original operation. Connector native
tool output/journal must independently show which complete scope was actually
delivered to the model. The genuine cross-room native journey remains a separate
gate; it cannot be inferred from HTTP qualification or this server-issued proof.
Reader provenance is the authenticated HTTP principal and its source-v2 transport
assertion checked against canonical current execution/context/resource rows. The
server does not independently observe the app-server pipe or certify that an
asserted tool/call actually reached a native model invocation.

Freshness requires the same current authority/agent/host/delivery/generation/
claim/request/context/role/thread/turn/native-goal identity at later use. Tool,
call ID and argument digest may differ between the explicit briefing and report
calls; their actual values remain in original receipts. A read expires after
15minutes. Any relevant decision revision or original scope change invalidates
it. A new higher global sequence alone never refreshes it.

Original completion/result/attempt, independently stopped original executor,
current independently live execution, current original work owner, cooperation,
posting/reading in original and current rooms, private context, role/placement,
present cancellation and competing execution fences remain required. Reading the
prior scope never changes active participation or re-admits the old assignment.
Submission also holds on conflicted decisions anywhere in the exact verified
scope, including the parent-goal room. A complete read of a conflict is evidence
of that conflict, not permission to submit past it.

For new reconciliations the server derives `briefingSequence` from the verified
original work-room snapshot. An optional caller scalar is recorded separately as
`callerBriefingSequence`; it supplies no scope authority. The additive v2 audit
contains `scopeVerification` with the exact server proof, original scope, reader,
all decisions/scopes, timestamps and provenance. The original attempt's historical
briefing sequence is preserved. Existing effect review, artifact scope/hash
attestation and unresolved-control conditions still apply.

Only the original work's report fields are submitted. Business acceptance remains
a separate authorized independent review. That review also compares the exact
current relevant decision manifest, including the parent-goal scope. Decisions
changed after submission fence acceptance without rewriting original history.

Idempotent report POST returns its original accepted receipt after present
ownership/read/post/cancellation checks; it neither performs a new report nor
refreshes scope evidence. Historical legacy report wire without the new proof
cannot create a new report. Its original command GET remains evidence-only.
Legacy submitted reconciliation audits without scoped evidence also cannot gain
new acceptance. Existing historical receipts and accepted states are preserved;
any necessary rework follows explicit manager/reviewer policy, never automatic
conversion of a scalar into a scoped read.

## Audit and failures

Scope page events are `reportScopeProtocol`, private to the reader in the original
work room. They are excluded from conversation before pagination and projected as
`protocolAudit` with `conversation=false,actionCard=false` in all-events reads.
Original native call/operation and source issuer attribution remain intact. No
activity item, lifecycle/completion or second action card is fabricated, and
private original-room decisions are not republished into current room B.
The r03 registered-target and patch one-shot contracts remain unchanged.

The new read/reconciliation handlers return `ReportScopeFailure` with
`error,message? ,newExecutionAdmitted:false,receiptOnly:true`:400
`invalid_scope_request`,403 `authority_denied`,409 `source_or_scope_changed` or
specific conflicts including `scope_read_missing`, `scope_read_required`,
`scope_read_incomplete`, `scope_page_order`, `scope_read_complete`,
`scope_expired`, `foreign_scope_reader`, `original_scope_changed`,
`scope_decisions_changed`, `scope_proof_changed`, `original_scope_closed`,
`work_changed`, `scope_too_large` and `operation_changed`. Initial authentication
and inherited pre-handler evidence-path/read-URL errors retain generic boundary
responses. No failure, receipt lookup, timeout or5xx authorizes execution/retry.

`tests/canonical_collaboration_harness.py --scenario report-scope` runs a fresh
owned RAM PostgreSQL/authenticated HTTP journey: completed unreported A work,
later B participation, changed A and parent-goal decisions, paging, bad/scalar/
foreign/stale evidence, current revocation/cancellation, exact valid report and
separate review, immutable original identities/effects/results and actual RAM
counters. Every source/controller/executor/native lifecycle input is explicitly
SYNTHETIC. No live migration, model/native start or runtime activation is performed.
