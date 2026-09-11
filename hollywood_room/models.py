"""Validated public commands. Identity is supplied by the authenticated service."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid")
    commandId: str = Field(min_length=1, max_length=128)
    task: str = Field(default="lobby", pattern=r"^[a-z0-9_-]{1,64}$")
    body: str = Field(min_length=1, max_length=3500)
    targets: list[str] = Field(default_factory=list, max_length=8)
    visibility: Literal["room", "direct"] = "room"
    replyTo: int | None = Field(default=None, ge=1)


class Control(BaseModel):
    model_config = ConfigDict(extra="forbid")
    commandId: str = Field(min_length=1, max_length=128)
    task: str = Field(pattern=r"^[a-z0-9_-]{1,64}$")
    agent: str = Field(pattern=r"^[a-z0-9_-]{1,64}$")
    action: Literal["pause", "resume", "interrupt"]


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
    "coordinator": "Plan, assign, track dependencies and integrate the team's work.",
    "maya": "Implement client interfaces and usability.",
    "theo": "Implement server behavior, persistence and protocols.",
    "rowan": "Review designs and changes; test edge cases and report evidence.",
}
