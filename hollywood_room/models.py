"""Validated public commands. Identity is supplied by the authenticated service."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid")
    commandId: str = Field(min_length=1, max_length=128)
    task: str = Field(default="lobby", pattern=r"^[a-z0-9_-]{1,64}$")
    body: str = Field(min_length=1, max_length=3500)
    targets: list[str] = Field(default_factory=list)
    visibility: Literal["room", "direct"] = "room"
    replyTo: int | None = Field(default=None, ge=1)


class Control(BaseModel):
    model_config = ConfigDict(extra="forbid")
    commandId: str = Field(min_length=1, max_length=128)
    task: str = Field(pattern=r"^[a-z0-9_-]{1,64}$")
    agent: str = Field(pattern=r"^[a-z0-9_-]{1,64}$")
    action: Literal["pause", "resume", "interrupt"]


class ConversationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    commandId: str = Field(min_length=1, max_length=128)
    task: str = Field(pattern=r"^[a-z0-9_-]{1,64}$")
    agent: str = Field(pattern=r"^[a-z0-9_-]{1,64}$")
    label: str = Field(default="New conversation", min_length=1, max_length=100)


class ConversationControl(BaseModel):
    model_config = ConfigDict(extra="forbid")
    commandId: str = Field(min_length=1, max_length=128)
    action: Literal["attach", "detach", "stop", "resume"]


class LimitReset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    commandId: str = Field(min_length=1, max_length=128)
    expectedAccountKey: str = Field(min_length=64, max_length=64)
    conversationId: str | None = Field(default=None, max_length=128)


class ExternalAssociation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    commandId: str = Field(min_length=1, max_length=128)
    expectedOwnerId: str = Field(min_length=64, max_length=64)
    task: str = Field(pattern=r"^[a-z0-9_-]{1,64}$")
    agent: str = Field(pattern=r"^[a-z0-9_-]{1,64}$")
    action: Literal["associate", "dissociate"]


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    commandId: str = Field(min_length=1, max_length=128)
    body: str = Field(min_length=1, max_length=3500)


class Device(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,128}$")
    fid: str = Field(min_length=1, max_length=256)


class ApprovalAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["accept", "decline"]


TEAM = {
    "coordinator": "Lead user interaction, assign work, track dependencies and review team results.",
    "maya": "Implement client interfaces and usability.",
    "theo": "Implement server behavior, persistence and protocols.",
    "rowan": "Review designs and changes; test edge cases and report evidence.",
}


class TeamCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z0-9_-]{1,59}$")
    name: str = Field(min_length=1, max_length=100)
    coordinator: str = Field(pattern=r"^[a-z0-9_-]{1,64}$")
    role: str = Field(min_length=1, max_length=2000)


class TeammateCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z0-9_-]{1,64}$")
    role: str = Field(min_length=1, max_length=2000)


class TaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z0-9_-]{1,64}$")
    title: str = Field(min_length=1, max_length=100)
    commandId: str = Field(min_length=1, max_length=128)
    body: str = Field(min_length=1, max_length=3500)
    targets: list[str] = Field(default_factory=list)


class Dismiss(BaseModel):
    model_config = ConfigDict(extra="forbid")
    commandId: str = Field(min_length=1, max_length=128)


class RoomCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task: str = Field(pattern=r"^[a-z0-9_-]{1,64}$")
    title: str = Field(min_length=1, max_length=200)
    purpose: str = Field(default="", max_length=4000)
