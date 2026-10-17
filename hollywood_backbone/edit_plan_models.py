"""Typed protected-bridge plan and one-shot mutation protocol."""
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import Field

from .collaboration_models import Input

CAPABILITY = "canonical-edit-plans/v1"
SHA = r"^[a-f0-9]{64}$"
REVISION = r"^([a-f0-9]{40}|[a-f0-9]{64})$"
CONTENT = r"^(absent|[a-f0-9]{64})$"


class Participant(Input):
    workId: UUID
    workRevision: int = Field(ge=1, strict=True)
    sourceRevision: str = Field(pattern=REVISION)


class File(Input):
    relativePath: str = Field(min_length=1, max_length=512)
    claimId: UUID
    claimRevision: int = Field(ge=1, strict=True)
    beforeSha256: str = Field(pattern=CONTENT)
    beforeSize: int = Field(ge=0, le=16777216, strict=True)
    afterSha256: str = Field(pattern=CONTENT)
    afterSize: int = Field(ge=0, le=16777216, strict=True)


class Slice(Input):
    sliceId: UUID
    workId: UUID
    relativePath: str = Field(min_length=1, max_length=512)
    beforeSha256: str = Field(pattern=CONTENT)
    byteStart: int = Field(ge=0, le=16777216, strict=True)
    byteEnd: int = Field(ge=0, le=16777216, strict=True)
    hunkSha256: str = Field(pattern=SHA)
    artifactSha256: str = Field(pattern=SHA)
    artifactSize: int = Field(ge=1, le=16777216, strict=True)


class Manifest(Input):
    logicalSource: str = Field(min_length=1, max_length=128)
    sourceRevision: str = Field(pattern=REVISION)
    integratorWorkId: UUID
    participants: list[Participant] = Field(min_length=1, max_length=8)
    files: list[File] = Field(min_length=1, max_length=8)
    slices: list[Slice] = Field(min_length=1, max_length=16)
    patchSha256: str = Field(pattern=SHA)


class Artifact(Input):
    sliceId: UUID
    relativePath: str = Field(min_length=1, max_length=512)
    sha256: str = Field(pattern=SHA)
    size: int = Field(ge=1, le=16777216, strict=True)


class ObservedFile(Input):
    relativePath: str = Field(min_length=1, max_length=512)
    sha256: str = Field(pattern=CONTENT)
    size: int = Field(ge=0, le=16777216, strict=True)


class Command(Input):
    planId: UUID
    planRevision: int = Field(ge=0, strict=True)
    workId: UUID
    workRevision: int = Field(ge=1, strict=True)
    briefingSequence: int = Field(ge=0, strict=True)
    note: str = Field(min_length=1, max_length=1000)


class Propose(Command):
    action: Literal["propose"]
    planRevision: int = Field(ge=0, le=0, strict=True)
    manifest: Manifest


class Revise(Command):
    action: Literal["revise"]
    manifest: Manifest


class Consent(Command):
    action: Literal["consent"]
    consentRevision: int = Field(ge=0, strict=True)
    manifestSha256: str = Field(pattern=SHA)
    sourceRevision: str = Field(pattern=REVISION)
    artifacts: list[Artifact] = Field(max_length=16)
    imports: list[Artifact] = Field(max_length=16)
    participantConsentDigest: str | None
    settlementDigest: str | None


class Revoke(Command):
    action: Literal["revoke"]
    consentRevision: int = Field(ge=1, strict=True)


class Preflight(Command):
    action: Literal["preflight"]
    permitId: UUID
    manifestSha256: str = Field(pattern=SHA)
    consentSetDigest: str = Field(pattern=SHA)
    patchSha256: str = Field(pattern=SHA)
    sourceRevision: str = Field(pattern=REVISION)
    filesObserved: list[ObservedFile] = Field(min_length=1, max_length=8)
    leaseSeconds: int = Field(ge=1, le=30, strict=True)


class PermitCommand(Command):
    permitId: UUID
    permitRevision: int = Field(ge=1, strict=True)


class Begin(PermitCommand):
    action: Literal["begin"]
    patchSha256: str = Field(pattern=SHA)
    sourceRevision: str = Field(pattern=REVISION)
    filesObserved: list[ObservedFile] = Field(min_length=1, max_length=8)


class Settle(PermitCommand):
    action: Literal["settle"]
    outcome: Literal["applied", "notApplied", "unknown"]
    patchSha256: str = Field(pattern=SHA)
    sourceRevision: str = Field(pattern=REVISION)
    filesObserved: list[ObservedFile] = Field(min_length=1, max_length=8)


class Reconcile(PermitCommand):
    action: Literal["reconcile"]
    outcome: Literal["applied", "notApplied"]
    observationDigest: str = Field(pattern=SHA)
    effectDisposition: Literal["reviewed"]


class Withdraw(Command):
    action: Literal["withdraw"]


class EditPlanCall(Input):
    operationId: str = Field(min_length=1, max_length=128)
    bridgeRevision: int = Field(ge=1, strict=True)
    source: dict
    executorId: UUID
    descriptorDigest: str = Field(pattern=SHA)
    serviceBindingDigest: str = Field(pattern=SHA)
    channel: Literal["captured-app-server-rpc/v1"]
    command: Annotated[Propose | Revise | Consent | Revoke | Preflight | Begin | Settle | Reconcile | Withdraw,
                       Field(discriminator="action")]


class MutationObservation(Input):
    operationId: str = Field(min_length=1, max_length=128)
    bridgeRevision: int = Field(ge=1, strict=True)
    permitId: UUID
    permitRevision: int = Field(ge=1, strict=True)
    executorId: UUID
    descriptorDigest: str = Field(pattern=SHA)
    serviceBindingDigest: str = Field(pattern=SHA)
    channel: Literal["stopped-executor-observation/v1"]
    outcome: Literal["applied", "notApplied", "unknown"]
    filesObserved: list[ObservedFile] = Field(min_length=1, max_length=8)
    note: str = Field(min_length=1, max_length=1000)


class ApplyAdmission(Input):
    mayApply: Literal[True]
    permitId: UUID
    permitRevision: int = Field(ge=1, strict=True)
    expiresAt: str
    sourceDigest: str = Field(pattern=SHA)
    patchSha256: str = Field(pattern=SHA)
    files: list[File] = Field(min_length=1, max_length=8)
    oneShotOriginalResponseOnly: Literal[True]


class EditPlanReply(Input):
    # Canonical receipts are extensible domain snapshots, not an admission token.
    receipt: dict[str, Any]
    admission: ApplyAdmission | None
    replayed: bool = Field(strict=True)


class EditFailure(Input):
    error: str
    mayApply: Literal[False]
    message: str | None = None
    retry: Literal["read-original-operation-or-reconcile"] | None = None
