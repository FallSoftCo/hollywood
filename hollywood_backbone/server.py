"""Authenticated Hollywood API. It does not import or launch a model runtime."""

import argparse
import asyncio
import json
from pathlib import Path
from typing import Literal
from uuid import UUID

from aiohttp import web
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from psycopg import errors as pg_errors

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


async def room_create(request):
    p = await body(request, Room)
    return await invoke(request, "create_room", str(p.id), p.name, p.purpose, str(p.ownerId))


async def principal_create(request):
    p = await body(request, Principal)
    return await invoke(request, "principal", str(p.id), p.name, p.kind)


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
    return await invoke(
        request,
        "history",
        str(UUID(request.match_info["room"])),
        max(0, int(request.query.get("after", "0"))),
        min(100, max(1, int(request.query.get("limit", "100")))),
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
    return await invoke(request, "claim", p.operationId, str(p.hostId))


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


def application(store):
    app = web.Application(middlewares=[boundary], client_max_size=16384)
    app[STORE] = store
    app.add_routes(
        [
            web.get("/health", health),
            web.get("/hollywood/v3/rooms", rooms),
            web.post("/hollywood/v3/rooms", room_create),
            web.post("/hollywood/v3/principals", principal_create),
            web.put("/hollywood/v3/rooms/{room}/membership", membership),
            web.post("/hollywood/v3/rooms/{room}/messages", post),
            web.get("/hollywood/v3/rooms/{room}/history", history),
            web.get("/hollywood/v3/rooms/{room}/events", stream),
            web.get("/hollywood/v3/commands/{operation}", command),
            web.post("/hollywood/v3/placements", placement),
            web.post("/hollywood/v3/connector/claim", claim),
            web.post("/hollywood/v3/deliveries/{delivery}/transition", transition),
            web.post("/hollywood/v3/deliveries/{delivery}/cancel", cancel),
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
