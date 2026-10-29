"""Shared canonical message schema for HTTP and authenticated backplane calls."""
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceReferences(Input):
    version: int = Field(strict=True, ge=1, le=1)
    deliveryId: UUID
    eventIds: list[UUID] = Field(max_length=8)
    contextOperationIds: list[str] = Field(max_length=8)


class Message(Input):
    operationId: str = Field(min_length=1, max_length=128)
    kind: Literal["message", "request"]
    body: str = Field(min_length=1, max_length=6000)
    audience: Literal["room", "direct"] = "room"
    recipientId: UUID | None = None
    sourceEventId: UUID | None = None
    expectsReply: bool = False
    evidenceReferences: EvidenceReferences | None = None


