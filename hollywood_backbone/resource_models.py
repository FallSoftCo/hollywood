"""Additive canonical resource selection, independent of mutation admission."""
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from .collaboration_models_base import Input

CAPABILITY = 'canonical-agent-resources/v1'


class ResourceDefault(Input):
    operationId: str = Field(min_length=1, max_length=128)
    revision: int = Field(ge=0, strict=True)
    state: Literal['selected', 'cleared']
    workspaceId: UUID | None
    workspaceRevision: int | None = Field(ge=1, strict=True)
    grantRevision: int | None = Field(ge=1, strict=True)

    @model_validator(mode='after')
    def exact_selection(self):
        values = (self.workspaceId, self.workspaceRevision, self.grantRevision)
        if self.state == 'selected' and any(value is None for value in values):
            raise ValueError('Selection requires exact resource and access revisions')
        if self.state == 'cleared' and any(value is not None for value in values):
            raise ValueError('Cleared routing has no implicit replacement')
        return self
