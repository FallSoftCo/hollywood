"""Explicit human draft inputs, separate from agent-purpose runtime commands."""
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class SeedCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    operationId: str = Field(min_length=1, max_length=128)


class SeedMark(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operationId: str = Field(min_length=1, max_length=128)
    revision: int = Field(strict=True, ge=1)
    reference: str = Field(min_length=1, max_length=42)
    sourceRevision: int = Field(strict=True, ge=1)
    sourceSha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    keep: bool = Field(strict=True)
    reason: str = Field(min_length=1, max_length=500)
