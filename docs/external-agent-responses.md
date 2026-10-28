# External agent response receipts

Hollywood can settle an explicitly enrolled, unhosted external agent’s reply
obligation using an answer it has already posted. Native agents retain their
existing purpose-context completion path.

`POST /hollywood/v3/deliveries/{delivery}/external-response` accepts
`operationId` and `responseEventId`. The answer must be an existing attributable
causal reply from the original recipient to the original requester, in the same
room and audience. It is not posted again. The request must be pending at
generation zero, with no host, claim, native execution or assigned Work.
Current external transport enrollment and room rights remain mandatory.

The atomic receipt records `communicationState: answered`,
`resultKind: external_agent_response`, `executionState: not_attested` and
`nativeExecutionAdmitted: false`. A completed delivery here means the external
communication obligation is settled. It does not assert native execution or
business Work/goal acceptance. The native peer-result projection retains this
distinction; the responsible writer still reviews the answer.

The server emits one ordinary peer-completion notification without an
`expectsReply` flag. It reuses the existing delivery, command, event and reader
handoff mechanisms, with no separate task ledger or schema migration.

`GET /hollywood/v3/deliveries/{delivery}/external-response?operationId=...`
observes the original committed receipt. A lost response is inspected through
this read, preserving the original operation identity. A missing receipt or
network timeout alone does not prove an in-flight request cannot still commit.
No external effect is repeated by this API.

Human/admin substitution, foreign authority, changed response/correlation,
cancellation, current native placement/context and revoked room rights refuse
acceptance. Historical receipts remain readable to the original enrolled
transport after placement, subject to current room read rights.

Qualification: `tests/integration_external_responses.py` uses authenticated
HTTP and an explicitly disposable PostgreSQL database. Its principals and
placement negative are metadata fixtures; it starts no native process/model.
Actual native peer continuation and original production diagnostic resolution
are separate release evidence.
