"""Placement admission control, separate from enrollment and executor drain."""
from uuid import UUID

from pydantic import Field

from .collaboration_models_base import Input

CAPABILITY = 'canonical-placement-control/v1'


class PlacementState(Input):
    operationId: str = Field(min_length=1, max_length=128)
    revision: int = Field(ge=1, strict=True)
    hostId: UUID
    enabled: bool = Field(strict=True)
