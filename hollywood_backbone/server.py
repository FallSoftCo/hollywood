"""Authenticated Hollywood API. It does not import or launch a model runtime."""

import argparse
import asyncio
import json
from pathlib import Path
from typing import Literal
from uuid import UUID

from aiohttp import web
from psycopg import errors as pg_errors
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .store import Conflict, Denied, Store


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Message(Input):
    operationId: str = Field(min_length=1, max_length=128)
    kind: Literal["message", "request"]
    body: str = Field(min_length=1, max_length=6000)
    audience: Literal["room", "direct"] = "room"
    recipientId: UUID | None = None
    sourceEventId: UUID | None = None


class Principal(Input):
    id: UUID
    name: str = Field(min_length=1, max_length=128)
    kind: Literal["human", "agent"]
    enrollmentCredential: str = Field(pattern=r"^[A-Za-z0-9_-]{40,128}$")


class Room(Input):
    id: UUID
    name: str = Field(min_length=1, max_length=200)
    purpose: str = Field(min_length=1, max_length=4000)
    ownerId: UUID


class Membership(Input):
    principalId: UUID
    can_read: bool
    can_post: bool
    can_request: bool
    revision: int = Field(ge=0)


class Placement(Input):
    hostId: UUID
    name: str = Field(min_length=1, max_length=128)
    identityFingerprint: str = Field(min_length=16, max_length=512)
    capabilities: dict
    agentId: UUID


class Claim(Input):
    capabilities: list[str] = Field(default_factory=list, max_length=16)
    operationId: str = Field(min_length=1, max_length=128)
    hostId: UUID


class Transition(Input):
    operationId: str = Field(min_length=1, max_length=128)
    claimId: str = Field(min_length=1, max_length=128)
    generation: int = Field(ge=1)
    state: Literal["running", "completed", "failed", "unknown", "cancelled"]
    nativeThread: str | None = Field(default=None, max_length=128)
    nativeTurn: str | None = Field(default=None, max_length=128)
    body: str | None = Field(default=None, max_length=6000)


class Role(Input):
    nativeGoalScope: Literal["role", "work"] | None = None
    primary: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$")
    purpose: str = Field(min_length=1, max_length=4000)
    homeRoomId: UUID | None = None
    revision: int = Field(ge=0)


class QuestionOption(Input):
    label: str = Field(min_length=1, max_length=200)
    description: str = Field(max_length=1000)


class Question(Input):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,128}$")
    header: str = Field(min_length=1, max_length=64)
    question: str = Field(min_length=1, max_length=2000)
    isOther: bool = True
    isSecret: Literal[False] = False
    options: list[QuestionOption] | None = Field(default=None, max_length=12)


class QuestionDetails(Input):
    questions: list[Question] = Field(min_length=1, max_length=3)
    isBlocking: bool = True


class ApprovalDetails(Input):
    nativeContext: dict = Field(default_factory=dict)
    command: str | None = Field(default=None, min_length=1, max_length=6000)
    cwd: str | None = Field(default=None, max_length=4096)
    reason: str | None = Field(default=None, max_length=2000)
    changes: list[dict] | None = Field(default=None, max_length=100)


class AttentionCreate(Input):
    id: UUID
    deliveryId: UUID
    claimId: str = Field(min_length=1, max_length=128)
    generation: int = Field(ge=1)
    nativeThread: str = Field(min_length=1, max_length=128)
    nativeTurn: str = Field(min_length=1, max_length=128)
    nativeSlot: str = Field(min_length=1, max_length=256)
    method: Literal[
        "hollywood/question",
        "item/tool/requestUserInput",
        "item/commandExecution/requestApproval",
        "item/fileChange/requestApproval",
    ]
    summary: str = Field(min_length=1, max_length=6000)
    request: dict


class AttentionScope(Input):
    authorityId: UUID
    attentionId: UUID
    deliveryId: UUID
    agentId: UUID
    roomId: UUID
    hostId: UUID
    generation: int = Field(ge=1)
    nativeThread: str
    nativeTurn: str
    requestDigest: str = Field(pattern=r"^[0-9a-f]{64}$")


class QuestionAnswer(Input):
    answers: list[str] = Field(min_length=1, max_length=12)


class QuestionResponse(Input):
    answers: dict[str, QuestionAnswer]


class ApprovalResponse(Input):
    decision: Literal["accept", "decline"]


class AttentionResponse(Input):
    operationId: str = Field(min_length=1, max_length=128)
    scope: AttentionScope
    response: QuestionResponse | ApprovalResponse


class AttentionAcknowledgement(Input):
    operationId: str = Field(min_length=1, max_length=128)
    claimId: str = Field(min_length=1, max_length=128)
    generation: int = Field(ge=1)
    scope: AttentionScope
    state: Literal["native_submitted", "closed", "expired", "unknown"]


class NativePlanStep(Input):
    step: str = Field(min_length=1, max_length=2000)
    status: Literal["pending", "inProgress", "completed"]


class NativeGoalProgress(Input):
    goalId: str = Field(min_length=1, max_length=128)
    status: Literal["active", "paused", "blocked", "usageLimited", "budgetLimited", "complete"]
    tokensUsed: int = Field(ge=0)
    timeUsedSeconds: int = Field(ge=0)
    tokenBudget: int | None = Field(default=None, ge=0)


class NativeGoalBinding(Input):
    bindingKind: Literal["role", "work", "coordination"] | None = None
    roleRevision: int | None = Field(default=None, ge=1)
    claimId: str = Field(min_length=1, max_length=128)
    generation: int = Field(ge=1)
    goalId: UUID | None = None
    workId: UUID | None = None
    nativeThread: str = Field(min_length=1, max_length=128)
    nativeGoalId: str = Field(min_length=1, max_length=128)
    contextId: str = Field(pattern=r"^[0-9a-f]{64}$")
    workspaceId: UUID
    objectiveDigest: str = Field(pattern=r"^[0-9a-f]{64}$")


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


class GoalCreate(Input):
    operationId: str = Field(min_length=1, max_length=128)
    id: UUID
    sourceEventId: UUID
    title: str = Field(min_length=1, max_length=200)
    objective: str = Field(min_length=1, max_length=6000)
    acceptance: str = Field(min_length=1, max_length=4000)
    reviewBudget: int = Field(ge=0, le=16)


class WorkCreate(Input):
    operationId: str = Field(min_length=1, max_length=128)
    revision: int = Field(ge=1)
    id: UUID
    roomId: UUID
    ownerId: UUID | None
    specification: str = Field(min_length=1, max_length=6000)
    acceptance: str = Field(min_length=1, max_length=4000)
    dependencies: list[UUID] = Field(max_length=16)


class WorkflowRevision(Input):
    operationId: str = Field(min_length=1, max_length=128)
    revision: int = Field(ge=1)


class CooperationChange(WorkflowRevision):
    cooperationRevision: int = Field(ge=0)
    policy: Literal["leader_award", "kanban_pull"]
    reviewerId: UUID | None
    executorIds: list[UUID] = Field(min_length=1, max_length=32)


class ArtifactEvidence(Input):
    hostId: UUID
    workspaceId: UUID
    relativePath: str = Field(min_length=1, max_length=2048)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=0)


class WorkReport(WorkflowRevision):
    claimId: str = Field(min_length=1, max_length=128)
    generation: int = Field(ge=1)
    state: Literal["submitted", "blocked"]
    note: str = Field(min_length=1, max_length=4000)
    evidence: list[ArtifactEvidence] = Field(max_length=16)


class WorkReview(WorkflowRevision):
    decision: Literal["accepted", "rejected"]
    note: str = Field(min_length=1, max_length=4000)


class WorkCancel(WorkflowRevision):
    note: str = Field(min_length=1, max_length=4000)


class GoalState(WorkflowRevision):
    state: Literal["active", "paused", "accepted", "cancelled"]
    note: str = Field(min_length=1, max_length=4000)


class ReviewDispatch(Input):
    capabilities: list[str] = Field(default_factory=list, max_length=16)
    hostId: UUID


class SupervisionAck(Input):
    sequence: int = Field(ge=0, le=9007199254740991)
    revision: int = Field(ge=0)


STORE = web.AppKey("store", Store)


@web.middleware
async def boundary(request, handler):
    try:
        if request.path != "/health":
            authorization = request.headers.get("Authorization", "")
            if not authorization.startswith("Bearer "):
                raise Denied("Bearer authentication required")
            actor = await asyncio.to_thread(request.app[STORE].authenticate, authorization[7:])
            request["actor"] = actor["id"]
        return await handler(request)
    except Denied:
        return web.json_response({"error": "Access denied"}, status=403)
    except (Conflict, pg_errors.UniqueViolation):
        return web.json_response({"error": "Identity, revision or operation conflict"}, status=409)
    except (ValidationError, ValueError, TypeError, KeyError):
        return web.json_response({"error": "Invalid request"}, status=400)


async def body(request, model):
    return model.model_validate(await request.json())


async def invoke(request, method, *args):
    value = await asyncio.to_thread(getattr(request.app[STORE], method), request["actor"], *args)
    return web.json_response(value)


async def health(request):
    return web.json_response({"service": "hollywood", "api": 3, "storage": "postgresql"})


async def rooms(request):
    return await invoke(request, "catalog")


async def room_detail(request):
    return await invoke(request, "room_detail", str(UUID(request.match_info["room"])))


async def room_create(request):
    p = await body(request, Room)
    return await invoke(request, "create_room", str(p.id), p.name, p.purpose, str(p.ownerId))


async def principal_create(request):
    p = await body(request, Principal)
    return await invoke(request, "principal", str(p.id), p.name, p.kind, p.enrollmentCredential)


async def principal_read(request):
    return await invoke(request, "principal_read", str(UUID(request.match_info["principal"])))


async def role_read(request):
    return await invoke(request, "role_read", str(UUID(request.match_info["agent"])))


async def membership(request):
    p = await body(request, Membership)
    if (p.can_post or p.can_request) and not p.can_read:
        raise ValueError("Posting and requesting require room read access")
    return await invoke(
        request,
        "membership",
        str(UUID(request.match_info["room"])),
        str(p.principalId),
        p.model_dump(include={"can_read", "can_post", "can_request"}),
        p.revision,
    )


async def post(request):
    p = await body(request, Message)
    if not p.body.strip() or len(p.body.encode()) > 6000:
        raise ValueError("Invalid message body")
    return await invoke(
        request,
        "post",
        str(UUID(request.match_info["room"])),
        p.operationId,
        p.model_dump(mode="json", exclude={"operationId"}),
    )


async def history(request):
    if "before" in request.query and "after" in request.query:
        raise ValueError("Choose one history direction")
    return await invoke(
        request,
        "history",
        str(UUID(request.match_info["room"])),
        max(0, int(request.query.get("after", "0"))),
        min(100, max(1, int(request.query.get("limit", "100")))),
        max(1, int(request.query["before"])) if "before" in request.query else None,
    )


async def stream(request):
    room = str(UUID(request.match_info["room"]))
    cursor = max(0, int(request.headers.get("Last-Event-ID", request.query.get("after", "0"))))
    store = request.app[STORE]
    # Authorize before opening a stream, and again for each subsequent page.
    page = await asyncio.to_thread(store.history, request["actor"], room, cursor, 100)
    response = web.StreamResponse(
        headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"}
    )
    await response.prepare(request)
    try:
        while True:
            for event in page["events"]:
                await response.write(
                    (
                        "id: "
                        + str(event["seq"])
                        + "\nevent: room\ndata: "
                        + json.dumps(event)
                        + "\n\n"
                    ).encode()
                )
            cursor = page["nextCursor"]
            await response.write(b": heartbeat\n\n")
            await asyncio.sleep(1)
            # Re-check token revocation as well as room permissions during streaming.
            await asyncio.to_thread(store.authenticate, request.headers["Authorization"][7:])
            page = await asyncio.to_thread(store.history, request["actor"], room, cursor, 100)
    except (ConnectionResetError, asyncio.CancelledError, Denied):
        return response


async def inbox(request):
    if set(request.query) - {"after", "limit"}:
        raise ValueError("Unknown inbox query")
    after = int(request.query["after"]) if "after" in request.query else None
    limit = int(request.query.get("limit", "100"))
    if (after is not None and not 0 <= after <= 9007199254740991) or not 1 <= limit <= 100:
        raise ValueError("Invalid inbox cursor or limit")
    pending = request.path.endswith("/pending")
    if pending and after is None:
        after = 0
    return await invoke(request, "inbox", after, limit, pending)


async def inbox_event(request):
    return await invoke(request, "inbox_event", str(UUID(request.match_info["event"])))


async def command(request):
    return await invoke(request, "command", request.match_info["operation"])


async def placement(request):
    p = await body(request, Placement)
    return await invoke(
        request,
        "placement",
        str(p.hostId),
        p.name,
        p.identityFingerprint,
        p.capabilities,
        str(p.agentId),
    )


async def claim(request):
    p = await body(request, Claim)
    return await invoke(request, "claim", p.operationId, str(p.hostId), p.capabilities)


async def connector_pending(request):
    return await invoke(request, "connector_pending", str(UUID(request.query["hostId"])))


async def transition(request):
    p = await body(request, Transition)
    return await invoke(
        request,
        "transition",
        str(UUID(request.match_info["delivery"])),
        p.operationId,
        p.model_dump(mode="json", exclude={"operationId"}),
    )


async def cancel(request):
    # Idempotent desired state. It cannot claim that an admitted effect was undone.
    return await invoke(request, "cancel", str(UUID(request.match_info["delivery"])))


async def delivery(request):
    return await invoke(request, "delivery", str(UUID(request.match_info["delivery"])))


async def context(request):
    return await invoke(request, "execution_context", str(UUID(request.match_info["delivery"])))


async def native_goal_binding(request):
    p = await body(request, NativeGoalBinding)
    return await invoke(
        request,
        "native_goal_binding",
        str(UUID(request.match_info["delivery"])),
        p.model_dump(mode="json"),
    )


async def activity(request):
    p = await body(request, Activity)
    payload = p.model_dump(mode="json")
    for key in ("plan", "nativeGoal"):
        if payload["item"].get(key) is None:
            payload["item"].pop(key, None)
    if len(json.dumps(payload).encode()) > 14000:
        raise ValueError("Native activity exceeds limit")
    if (p.phase == "started") != (p.item.status == "inProgress"):
        raise ValueError("Native activity phase and status disagree")
    return await invoke(
        request, "activity_append", str(UUID(request.match_info["delivery"])), payload
    )


async def role(request):
    p = await body(request, Role)
    return await invoke(
        request,
        "role",
        str(UUID(request.match_info["agent"])),
        p.primary,
        p.purpose,
        str(p.homeRoomId) if p.homeRoomId else None,
        p.revision,
        p.nativeGoalScope,
    )


async def attention_create(request):
    p = await body(request, AttentionCreate)
    if p.method in ("hollywood/question", "item/tool/requestUserInput"):
        details = QuestionDetails.model_validate(p.request)
        if len({q.id for q in details.questions}) != len(details.questions):
            raise ValueError("Question identities must be unique")
    else:
        details = ApprovalDetails.model_validate(p.request)
        if p.method == "item/commandExecution/requestApproval" and not details.command:
            raise ValueError("Complete command action required")
        if p.method == "item/fileChange/requestApproval" and not details.changes:
            raise ValueError("Complete file changes required")
    payload = p.model_dump(mode="json", exclude={"id"})
    payload["request"] = details.model_dump(mode="json")
    if len(json.dumps(payload).encode()) > 14000:
        raise ValueError("Attention action exceeds limit")
    return await invoke(request, "attention_create", str(p.id), payload)


async def attention_read(request):
    return await invoke(request, "attention_read", str(UUID(request.match_info["attention"])))


async def attention_inbox(request):
    return await invoke(
        request,
        "attention_inbox",
        str(UUID(request.match_info["room"])),
        max(0, int(request.query.get("after", "0"))),
        min(100, max(1, int(request.query.get("limit", "100")))),
    )


async def attention_response(request):
    p = await body(request, AttentionResponse)
    payload = p.model_dump(mode="json", exclude={"operationId"})
    if len(json.dumps(payload).encode()) > 14000:
        raise ValueError("Response exceeds limit")
    return await invoke(
        request,
        "attention_response",
        str(UUID(request.match_info["attention"])),
        p.operationId,
        payload,
    )


async def attention_acknowledge(request):
    p = await body(request, AttentionAcknowledgement)
    return await invoke(
        request,
        "attention_acknowledge",
        str(UUID(request.match_info["attention"])),
        p.operationId,
        p.model_dump(mode="json", exclude={"operationId"}),
    )


async def attention_command(request):
    operation = request.match_info["operation"]
    if not 1 <= len(operation) <= 128:
        raise ValueError("Invalid attention command")
    return await invoke(
        request, "attention_command", str(UUID(request.match_info["attention"])), operation
    )


async def workflow(request):
    action = request.match_info["action"]
    model = {
        "assign": WorkflowRevision,
        "claim": WorkflowRevision,
        "report": WorkReport,
        "review": WorkReview,
        "cancel": WorkCancel,
    }[action]
    p = await body(request, model)
    payload = p.model_dump(mode="json", exclude={"operationId"})
    if action == "report":
        from pathlib import PurePosixPath

        for evidence in payload["evidence"]:
            path = PurePosixPath(evidence["relativePath"])
            if path.is_absolute() or ".." in path.parts or "\\" in evidence["relativePath"]:
                raise ValueError("Evidence requires a relative workspace path")
    return await invoke(
        request,
        "workflow",
        "work." + action,
        str(UUID(request.match_info["work"])),
        p.operationId,
        payload,
    )


async def cooperation_change(request):
    p = await body(request, CooperationChange)
    if len(set(p.executorIds)) != len(p.executorIds):
        raise ValueError("Duplicate cooperation executor")
    return await invoke(
        request,
        "workflow",
        "goal.cooperation",
        str(UUID(request.match_info["goal"])),
        p.operationId,
        p.model_dump(mode="json", exclude={"operationId"}),
    )


async def goal_create(request):
    p = await body(request, GoalCreate)
    return await invoke(
        request,
        "workflow",
        "goal.create",
        str(UUID(request.match_info["room"])),
        p.operationId,
        p.model_dump(mode="json", exclude={"operationId"}),
    )


async def work_create(request):
    p = await body(request, WorkCreate)
    if len(set(p.dependencies)) != len(p.dependencies):
        raise ValueError("Duplicate dependencies")
    return await invoke(
        request,
        "workflow",
        "goal.work",
        str(UUID(request.match_info["goal"])),
        p.operationId,
        p.model_dump(mode="json", exclude={"operationId"}),
    )


async def goal_state(request):
    p = await body(request, GoalState)
    return await invoke(
        request,
        "workflow",
        "goal.state",
        str(UUID(request.match_info["goal"])),
        p.operationId,
        p.model_dump(mode="json", exclude={"operationId"}),
    )


async def goal_read(request):
    return await invoke(request, "goal_read", str(UUID(request.match_info["goal"])))


async def work_read(request):
    return await invoke(request, "work_read", str(UUID(request.match_info["work"])))


async def goal_list(request):
    if set(request.query) - {"after", "limit"}:
        raise ValueError("Unknown goal query")
    after = str(UUID(request.query["after"])) if "after" in request.query else None
    limit = int(request.query.get("limit", "100"))
    if not 1 <= limit <= 100:
        raise ValueError("Invalid goal limit")
    return await invoke(request, "goal_list", str(UUID(request.match_info["room"])), after, limit)


async def work_list(request):
    if set(request.query) - {"after", "limit"}:
        raise ValueError("Unknown work query")
    after = str(UUID(request.query["after"])) if "after" in request.query else None
    limit = int(request.query.get("limit", "100"))
    if not 1 <= limit <= 100:
        raise ValueError("Invalid work limit")
    by_goal = "goal" in request.match_info
    identity = str(UUID(request.match_info["goal" if by_goal else "room"]))
    return await invoke(request, "work_list", identity, after, limit, by_goal)


async def review_dispatch(request):
    p = await body(request, ReviewDispatch)
    return await invoke(request, "review_dispatch", str(p.hostId), p.capabilities)


async def supervision(request):
    if set(request.query) - {"after", "limit"}:
        raise ValueError("Unknown supervision query")
    after = int(request.query["after"]) if "after" in request.query else None
    limit = int(request.query.get("limit", "100"))
    if (after is not None and not 0 <= after <= 9007199254740991) or not 1 <= limit <= 100:
        raise ValueError("Invalid supervision cursor")
    return await invoke(request, "supervision", after, limit)


async def supervision_ack(request):
    p = await body(request, SupervisionAck)
    return await invoke(request, "supervision_ack", p.sequence, p.revision)


def application(store):
    app = web.Application(middlewares=[boundary], client_max_size=16384)
    app[STORE] = store
    app.add_routes(
        [
            web.get("/health", health),
            web.get("/hollywood/v3/rooms/{room}/goals", goal_list),
            web.post("/hollywood/v3/rooms/{room}/goals", goal_create),
            web.get("/hollywood/v3/goals/{goal}", goal_read),
            web.post("/hollywood/v3/goals/{goal}/cooperation", cooperation_change),
            web.post("/hollywood/v3/goals/{goal}/work", work_create),
            web.get("/hollywood/v3/goals/{goal}/work", work_list),
            web.get("/hollywood/v3/rooms/{room}/work", work_list),
            web.post("/hollywood/v3/goals/{goal}/state", goal_state),
            web.get("/hollywood/v3/work/{work}", work_read),
            web.post(
                "/hollywood/v3/work/{work}/{action:assign|claim|report|review|cancel}", workflow
            ),
            web.post("/hollywood/v3/connector/reviews", review_dispatch),
            web.get("/hollywood/v3/supervision", supervision),
            web.put("/hollywood/v3/supervision/cursor", supervision_ack),
            web.get("/hollywood/v3/inbox", inbox),
            web.get("/hollywood/v3/inbox/pending", inbox),
            web.get("/hollywood/v3/inbox/events/{event}", inbox_event),
            web.get("/hollywood/v3/rooms", rooms),
            web.get("/hollywood/v3/rooms/{room}", room_detail),
            web.post("/hollywood/v3/rooms", room_create),
            web.post("/hollywood/v3/principals", principal_create),
            web.get("/hollywood/v3/principals/{principal}", principal_read),
            web.put("/hollywood/v3/rooms/{room}/membership", membership),
            web.post("/hollywood/v3/rooms/{room}/messages", post),
            web.get("/hollywood/v3/rooms/{room}/history", history),
            web.get("/hollywood/v3/rooms/{room}/events", stream),
            web.get("/hollywood/v3/commands/{operation}", command),
            web.post("/hollywood/v3/placements", placement),
            web.post("/hollywood/v3/connector/claim", claim),
            web.get("/hollywood/v3/connector/pending", connector_pending),
            web.post("/hollywood/v3/deliveries/{delivery}/transition", transition),
            web.post("/hollywood/v3/deliveries/{delivery}/cancel", cancel),
            web.get("/hollywood/v3/deliveries/{delivery}", delivery),
            web.get("/hollywood/v3/connector/deliveries/{delivery}/context", context),
            web.post("/hollywood/v3/connector/deliveries/{delivery}/activity", activity),
            web.post(
                "/hollywood/v3/connector/deliveries/{delivery}/goal-binding", native_goal_binding
            ),
            web.put("/hollywood/v3/agents/{agent}/role", role),
            web.get("/hollywood/v3/agents/{agent}/role", role_read),
            web.post("/hollywood/v3/attention", attention_create),
            web.get("/hollywood/v3/attention/{attention}", attention_read),
            web.get("/hollywood/v3/attention/{attention}/commands/{operation}", attention_command),
            web.get("/hollywood/v3/rooms/{room}/attention", attention_inbox),
            web.post("/hollywood/v3/attention/{attention}/response", attention_response),
            web.post("/hollywood/v3/attention/{attention}/acknowledge", attention_acknowledge),
        ]
    )
    return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-config", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18768)
    args = parser.parse_args()
    store = Store(json.loads(args.database_config.read_text()))
    store.migrate()
    try:
        web.run_app(application(store), host="127.0.0.1", port=args.port, access_log=None)
    finally:
        store.close()


if __name__ == "__main__":
    main()
