"""Versioned server-issued original-work scope evidence; not model-read proof."""
from typing import Any, Literal
from uuid import UUID
from pydantic import Field
from .collaboration_models_base import Input

SHA = r"^[a-f0-9]{64}$"
CAPABILITY = "completed-report-scope/v1"


class ReportScopeRead(Input):
    schema_: Literal["completed-report-scope-read/v1"] = Field(alias="schema")
    operationId: str = Field(min_length=1, max_length=128)
    revision: int = Field(ge=1, strict=True)
    priorDeliveryId: UUID
    readId: UUID | None = None
    cursor: int = Field(default=0, ge=0, strict=True)
    pageSize: int = Field(default=32, ge=1, le=32, strict=True)


class OriginalReportScope(Input):
    authorityId: UUID
    agentId: UUID
    workId: UUID
    workRevision: int = Field(ge=1, strict=True)
    goalId: UUID
    goalRoomId: UUID
    goalSourceEventId: UUID
    goalDirectionSha256: str = Field(pattern=SHA)
    roomId: UUID
    requestId: UUID
    originalIssuerId: UUID
    originalIssuerKind: str
    deliveryId: UUID
    generation: int = Field(ge=1, strict=True)
    claimId: str
    hostId: UUID
    workspaceId: UUID
    contextId: str = Field(pattern=SHA)
    nativeThread: str
    nativeTurn: str
    originalResultEventId: UUID
    attemptNumber: int = Field(ge=1, strict=True)
    specificationSha256: str = Field(pattern=SHA)
    acceptanceSha256: str = Field(pattern=SHA)
    cooperationRevision: int | None


class ScopedDecision(Input):
    id: UUID
    roomId: UUID
    goalId: UUID | None
    key: str
    revision: int = Field(ge=1, strict=True)
    state: Literal["active", "retracted", "conflicted"]
    body: str
    issuerId: UUID
    issuerKind: str
    sourceEventId: UUID
    eventId: UUID
    sequence: int = Field(ge=1, strict=True)


class ReportScopeProof(Input):
    schema_: Literal["completed-report-scope-proof/v1"] = Field(alias="schema")
    readId: UUID
    revision: int = Field(ge=1, strict=True)
    snapshotSha256: str = Field(pattern=SHA)
    scopeSha256: str = Field(pattern=SHA)
    decisionsSha256: str = Field(pattern=SHA)
    complete: Literal[True]


class ReportScopeBriefing(Input):
    schema_: Literal["completed-report-briefing/v1"] = Field(alias="schema")
    readId: UUID
    revision: int = Field(ge=1, strict=True)
    scope: OriginalReportScope
    reader: dict[str, Any]
    decisionScopes: list[dict[str, Any]]
    asOfSequence: int = Field(ge=0, strict=True)
    decisionSequence: int = Field(ge=0, strict=True)
    originalRoomDecisionSequence: int = Field(ge=0, strict=True)
    scopeSha256: str = Field(pattern=SHA)
    decisionsSha256: str = Field(pattern=SHA)
    snapshotSha256: str = Field(pattern=SHA)
    decisionCount: int = Field(ge=0, strict=True)
    coveredCount: int = Field(ge=0, strict=True)
    decisions: list[ScopedDecision]
    complete: bool = Field(strict=True)
    nextCursor: int | None
    scopeRead: ReportScopeProof | None
    issuedAt: str
    expiresAt: str
    contentAuthority: Literal["tool-data-with-attributable-issuers"]
    provenance: Literal["Authenticated scoped pages served by server; model receipt/comprehension not established"]
    newExecutionAdmitted: Literal[False]
    currentParticipationUnchanged: Literal[True]


class ReportScopeFailure(Input):
    error: str
    message: str | None = None
    newExecutionAdmitted: Literal[False]
    receiptOnly: Literal[True]


class ReportReconciliationReceipt(Input):
    accepted: Literal[True]
    goalId: UUID
    goalRevision: int = Field(ge=1, strict=True)
    workId: UUID
    workRevision: int = Field(ge=1, strict=True)
    state: Literal["submitted", "blocked"]
    requestEventId: UUID
    deliveryId: UUID
    reconciliation: dict[str, Any]
    eventId: UUID
