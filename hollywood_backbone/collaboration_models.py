"""Strict claim wire with registered per-action physical-resource attestation.

The envelope is produced by a separately enrolled protected LAX bridge. Native
tool arguments contain only `command`; source/physical identity is never theirs.
"""
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field
from .collaboration_models_base import Input
from .physical_models import PhysicalTarget


class BridgeConfiguration(Input):
    operationId: str = Field(min_length=1, max_length=128)
    revision: int = Field(ge=0, strict=True)
    controllerId: UUID
    state: Literal["active", "revoked"]
    protocol: Literal["canonical-physical-claims/v1", "canonical-edit-plans/v1", "canonical-edit-plans/v2"]
    adapterSha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    enrollmentSha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class Command(Input):
    workId: UUID
    workRevision: int = Field(ge=1, strict=True)
    claimId: UUID
    claimRevision: int = Field(ge=0, strict=True)
    briefingSequence: int = Field(ge=0, strict=True)
    note: str = Field(min_length=1, max_length=2000)


class Acquire(Command):
    action: Literal["acquire"]
    claimRevision: int = Field(ge=0, le=0, strict=True)
    relativePath: str = Field(min_length=1, max_length=1024)
    kind: Literal["file", "directory"]
    sourceRevision: str = Field(pattern=r"^([a-f0-9]{40}|[a-f0-9]{64})$")
    contentSha256: str = Field(pattern=r"^(absent|[a-f0-9]{64})$")
    leaseSeconds: int = Field(ge=1, le=3600, strict=True)


class Renew(Command):
    action: Literal["renew"]
    leaseSeconds: int = Field(ge=1, le=3600, strict=True)


class Release(Command):
    action: Literal["release"]
    effectDisposition: Literal["reviewed", "unknown"]


class Handoff(Command):
    action: Literal["handoff"]
    targetWorkId: UUID
    targetWorkRevision: int = Field(ge=1, strict=True)
    effectDisposition: Literal["reviewed", "unknown"]


class Accept(Command):
    action: Literal["accept"]
    leaseSeconds: int = Field(ge=1, le=3600, strict=True)


class Inspect(Command):
    action: Literal["inspect"]


class CollaborationCall(Input):
    target: PhysicalTarget
    operationId: str = Field(min_length=1, max_length=128)
    bridgeRevision: int = Field(ge=1, strict=True)
    source: dict
    executorId: UUID
    descriptorDigest: str = Field(pattern=r"^[a-f0-9]{64}$")
    serviceBindingDigest: str = Field(pattern=r"^[a-f0-9]{64}$")
    channel: Literal["captured-app-server-rpc/v1"]
    command: Annotated[Acquire | Renew | Release | Handoff | Accept | Inspect,
                       Field(discriminator="action")]
