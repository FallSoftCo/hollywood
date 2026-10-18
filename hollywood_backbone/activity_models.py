"""One activity wire contract for legacy and purpose-context execution logs."""
import json
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")

class NativePlanStep(Input):
    step: str = Field(min_length=1, max_length=2000)
    status: Literal["pending", "inProgress", "completed"]


class NativeGoalProgress(Input):
    goalId: str = Field(min_length=1, max_length=128)
    status: Literal["active", "paused", "blocked", "usageLimited", "budgetLimited", "complete"]
    tokensUsed: int = Field(ge=0)
    timeUsedSeconds: int = Field(ge=0)
    tokenBudget: int | None = Field(default=None, ge=0)


class ActivityItem(Input):
    nativeType: str = Field(min_length=1, max_length=80)
    plan: list[NativePlanStep] | None = Field(default=None, max_length=50)
    nativeGoal: NativeGoalProgress | None = None
    title: str = Field(min_length=1, max_length=1000)
    status: Literal["inProgress", "completed", "failed", "declined", "unknown"]
    command: str | None = Field(default=None, max_length=4000)
    cwd: str | None = Field(default=None, max_length=2048)
    output: str | None = Field(default=None, max_length=6000)
    outputTruncated: bool = False
    detailsTruncated: bool = False
    exitCode: int | None = None
    durationMs: int | None = Field(default=None, ge=0)
    changes: list[dict] | None = Field(default=None, max_length=50)


class Activity(Input):
    claimId: str = Field(min_length=1, max_length=128)
    generation: int = Field(ge=1)
    nativeThread: str = Field(min_length=1, max_length=128)
    nativeTurn: str = Field(min_length=1, max_length=128)
    itemId: str = Field(min_length=1, max_length=256)
    phase: Literal["started", "completed"]
    nativeItemDigest: str = Field(pattern=r"^[0-9a-f]{64}$")
    item: ActivityItem


def normalize(payload):
    value = Activity.model_validate(payload).model_dump(mode="json")
    for key in ("plan", "nativeGoal"):
        if value["item"].get(key) is None:
            value["item"].pop(key, None)
    if len(json.dumps(value).encode()) > 14000:
        raise ValueError("Native activity exceeds limit")
    if (value["phase"] == "started") != (value["item"]["status"] == "inProgress"):
        raise ValueError("Native activity phase and status disagree")
    return value
