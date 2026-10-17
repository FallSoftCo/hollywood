"""Versioned ordinary-delivery mutation protocol; no business-plan identifiers."""
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, model_validator

from .collaboration_models_base import Input
from .edit_plan_models import CONTENT, REVISION, SHA, ObservedFile
from .physical_models import PhysicalTarget

CAPABILITY = 'canonical-delivery-edits/v1'


class AgentWorkspaceGrant(Input):
    operationId: str = Field(min_length=1, max_length=128)
    revision: int = Field(ge=0, strict=True)
    workspaceRevision: int = Field(ge=1, strict=True)
    executionHost: UUID
    state: Literal['active', 'revoked']
    canRead: bool = Field(strict=True)
    canWrite: bool = Field(strict=True)

    @model_validator(mode='after')
    def write_reads(self):
        if self.canWrite and not self.canRead:
            raise ValueError('Writing requires baseline read permission')
        return self


class DeliveryFile(Input):
    relativePath: str = Field(min_length=1, max_length=512)
    beforeSha256: str = Field(pattern=CONTENT)
    beforeSize: int = Field(ge=0, le=16777216, strict=True)
    afterSha256: str = Field(pattern=CONTENT)
    afterSize: int = Field(ge=0, le=16777216, strict=True)


class DeliveryCommand(Input):
    permitId: UUID
    briefingSequence: int = Field(ge=0, strict=True)
    note: str = Field(min_length=1, max_length=1000)
    sourceRevision: str | None = Field(pattern=REVISION)
    patchSha256: str = Field(pattern=SHA)
    filesObserved: list[ObservedFile] = Field(min_length=1, max_length=8)


class DeliveryPreflight(DeliveryCommand):
    action: Literal['preflight']
    files: list[DeliveryFile] = Field(min_length=1, max_length=8)
    leaseSeconds: int = Field(ge=1, le=30, strict=True)


class DeliveryBegin(DeliveryCommand):
    action: Literal['begin']
    permitRevision: int = Field(ge=1, strict=True)


class DeliverySettle(DeliveryCommand):
    action: Literal['settle']
    permitRevision: int = Field(ge=1, strict=True)
    outcome: Literal['applied', 'notApplied', 'unknown']


class DeliveryEditCall(Input):
    schema_: Literal['canonical-delivery-edits/v1'] = Field(alias='schema')
    scope: Literal['delivery']
    target: PhysicalTarget
    operationId: str = Field(min_length=1, max_length=128)
    bridgeRevision: int = Field(ge=1, strict=True)
    source: dict
    executorId: UUID
    descriptorDigest: str = Field(pattern=SHA)
    serviceBindingDigest: str = Field(pattern=SHA)
    channel: Literal['captured-app-server-rpc/v1']
    command: Annotated[DeliveryPreflight | DeliveryBegin | DeliverySettle, Field(discriminator='action')]
