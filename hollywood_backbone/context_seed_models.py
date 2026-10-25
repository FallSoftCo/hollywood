"""Explicit human draft inputs, separate from agent-purpose runtime commands."""
from typing import Literal
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


class SeedControl(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operationId: str = Field(min_length=1, max_length=128)
    action: Literal["prepare", "approve", "cancel", "revise"]
    revision: int = Field(strict=True, ge=1)
    replacementDraftId: UUID | None = None
    canonicalSha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    approvalDigest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
