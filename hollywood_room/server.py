"""Authenticated room API. Stop/rebuild clients without stopping this service."""

import argparse
import asyncio
import hmac
import json
import os
import re
import secrets
from pathlib import Path

from aiohttp import web
from pydantic import ValidationError

from .history import HistoryQuery, history_page
from .models import TEAM, Answer, ApprovalAnswer, Control, Device, Message
from .runtime import Runtime
from .store import Conflict, Store

SERVICE = web.AppKey("service", object)


class Service:
    def __init__(self, store, token, runtime=None, *, push_configured=False):
        self.store, self.token, self.runtime = store, token, runtime
        self.push_configured = push_configured

    def post(self, message, *, resolves=None):
        if not message.body.strip() or len(message.body.encode("utf-8")) > 6000:
            raise ValueError("Messages must contain text and fit within 6000 UTF-8 bytes")
        task, targets = message.task, message.targets
        if message.replyTo:
            source = self.store.get_event(message.replyTo)
            task = source["task"]
            targets = targets or (
                [source["author"]]
                if source["author"] in TEAM
                else source["data"].get("targets", [])
            )
        if not targets:
            match = re.match(r"^@?([A-Za-z][\w-]*)\s*[:,]", message.body)
            mention = re.match(r"^@([A-Za-z][\w-]*)\b", message.body)
            named = match or mention
            if named and (mention or named[1].lower() in TEAM):
                targets = [named[1].lower()]
            else:
                targets = ["coordinator"]
        if any(target not in TEAM for target in targets):
            raise ValueError("Unknown recipient. Available: " + ", ".join(TEAM))
        targets = list(dict.fromkeys(targets))
        if message.visibility == "direct" and len(targets) != 1:
            raise ValueError("A direct conversation requires exactly one teammate")
        visibility = f"direct:{targets[0]}" if message.visibility == "direct" else "room"
        if message.replyTo and source["visibility"] != "room":
            visibility = source["visibility"]
            if targets != [visibility.split(":", 1)[1]]:
                raise ValueError("A private reply cannot change recipients")
        return self.store.message(
            message.commandId,
            task=task,
            author="you",
            body=message.body,
            targets=targets,
            authority="user",
            visibility=visibility,
            reply_to=message.replyTo,
            resolves=resolves,
        )


@web.middleware
async def boundary(request, handler):
    service = request.app[SERVICE]
    if request.path != "/health" and not hmac.compare_digest(
        request.headers.get("Authorization", ""), f"Bearer {service.token}"
    ):
        raise web.HTTPUnauthorized()
    try:
        return await handler(request)
    except Conflict as exc:
        return web.json_response({"error": str(exc)}, status=409)
    except (ValueError, ValidationError, KeyError, IndexError) as exc:
        return web.json_response({"error": str(exc)[:1000]}, status=400)


def application(service):
    app = web.Application(middlewares=[boundary], client_max_size=16 * 1024)
    app[SERVICE] = service

    async def health(request):
        return web.json_response(
            {
                "service": "hollywood-room",
                "apiVersion": 2,
                "runtimeReady": bool(service.runtime and service.runtime.ready),
            }
        )

    async def snapshot(request):
        return web.json_response(
            {
                **service.store.snapshot(),
                "team": TEAM,
                "runtimeReady": bool(service.runtime and service.runtime.ready),
                "pushConfigured": service.push_configured,
            }
        )

    async def messages(request):
        return web.json_response(
            {
                "data": service.store.events(
                    after=int(request.query.get("after", 0)),
                    task=request.query.get("task"),
                    limit=int(request.query.get("limit", 100)),
                )
            }
        )

    async def post(request):
        return web.json_response(
            service.post(Message.model_validate(await request.json())), status=202
        )

    async def history(request):
        return web.json_response(
            history_page(service.store, HistoryQuery.model_validate(dict(request.query)))
        )

    async def detail(request):
        event_id = int(request.match_info["id"])
        rows = service.store.rows("SELECT state FROM attention WHERE id=?", (event_id,))
        return web.json_response(
            {
                "event": service.store.get_event(event_id),
                "attentionState": rows[0]["state"] if rows else None,
            }
        )

    async def receipt(request):
        rows = service.store.rows("SELECT * FROM command WHERE id=?", (request.match_info["id"],))
        if not rows:
            raise web.HTTPNotFound()
        event = rows[0]["event"]
        return web.json_response(
            {
                "event": service.store.get_event(event),
                "deliveries": service.store.rows("SELECT * FROM delivery WHERE event=?", (event,)),
            }
        )

    async def control(request):
        command = Control.model_validate(await request.json())
        if service.runtime is None:
            raise web.HTTPServiceUnavailable()
        return web.json_response(
            await service.runtime.control(
                command.task, command.agent, command.action, command_id=command.commandId
            )
        )

    async def answer(request):
        value = Answer.model_validate(await request.json())
        item_id = int(request.match_info["id"])
        rows = service.store.rows("SELECT * FROM attention WHERE id=?", (item_id,))
        if not rows:
            raise Conflict("Unknown attention item")
        if rows[0]["state"] != "open":
            prior = service.store.rows("SELECT event FROM command WHERE id=?", (value.commandId,))
            if prior and rows[0]["response"] == value.body:
                original = service.store.get_event(prior[0]["event"])
                if original["data"].get("replyTo") == item_id:
                    return web.json_response(original)
            raise Conflict("This attention item is no longer open")
        item = rows[0]
        if service.store.get_event(item_id)["kind"] == "approval":
            raise Conflict("Use the explicit approval response for this live request")
        result = service.post(
            Message(
                commandId=value.commandId,
                task=item["task"],
                body=value.body,
                targets=[item["agent"]],
                replyTo=item_id,
            ),
            resolves=item_id,
        )
        return web.json_response(result)

    async def approval(request):
        value = ApprovalAnswer.model_validate(await request.json())
        if service.runtime is None:
            raise Conflict("No live runtime owns this approval")
        return web.json_response(
            service.runtime.approvals.answer(int(request.match_info["id"]), value.decision)
        )

    async def events(request):
        cursor = int(request.headers.get("Last-Event-ID", request.query.get("after", 0)))
        response = web.StreamResponse(
            headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"}
        )
        await response.prepare(request)
        try:
            while True:
                batch = service.store.events(after=cursor)
                if batch:
                    for event in batch:
                        await response.write(
                            f"id: {event['id']}\ndata: {json.dumps(event)}\n\n".encode()
                        )
                        cursor = event["id"]
                else:
                    await response.write(b": keepalive\n\n")
                    await asyncio.sleep(1)
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        return response

    async def device(request):
        value = Device.model_validate(await request.json())
        with service.store.db:
            service.store.db.execute(
                """INSERT INTO device(id,token,after_event) VALUES(?,?,?)
                ON CONFLICT(id) DO UPDATE SET token=excluded.token,revoked=0""",
                (value.id, value.fid, service.store.snapshot()["cursor"]),
            )
        return web.json_response(
            {"id": value.id, "registered": True, "pushConfigured": service.push_configured}
        )

    async def revoke(request):
        with service.store.db:
            service.store.db.execute(
                "UPDATE device SET revoked=1 WHERE id=?", (request.match_info["id"],)
            )
        return web.json_response({"revoked": True})

    app.add_routes(
        [
            web.get("/health", health),
            web.get("/hollywood/v2/overview", snapshot),
            web.get("/hollywood/v2/messages", messages),
            web.get("/hollywood/v2/history", history),
            web.post("/hollywood/v2/messages", post),
            web.get("/hollywood/v2/messages/{id}", detail),
            web.get("/hollywood/v2/events", events),
            web.get("/hollywood/v2/commands/{id}", receipt),
            web.post("/hollywood/v2/control", control),
            web.post("/hollywood/v2/attention/{id}/answer", answer),
            web.post("/hollywood/v2/devices", device),
            web.delete("/hollywood/v2/devices/{id}", revoke),
            web.post("/hollywood/v2/approval/{id}/answer", approval),
        ]
    )
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--workspace-mode", choices=("worktree", "shared"), default="worktree")
    parser.add_argument("--codex", required=True, help="Absolute installed Codex binary path")
    parser.add_argument("--codex-home", type=Path)
    parser.add_argument("--model")
    parser.add_argument("--port", type=int, default=18766)
    parser.add_argument("--firebase-credentials", type=Path)
    parser.add_argument("--firebase-project")
    args = parser.parse_args()
    if args.firebase_credentials and not args.firebase_project:
        parser.error("--firebase-project is required with --firebase-credentials")
    state = args.state.resolve()
    os.umask(0o077)
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    state.chmod(0o700)
    token_file = state / "client-token"
    if not token_file.exists():
        fd = os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(secrets.token_urlsafe(32))
    store = Store(state / "room.db")
    runtime = Runtime(
        store,
        binary=str(Path(args.codex).resolve()),
        home=args.codex_home or state / "codex-home",
        state=state / "runtime",
        workspace=args.workspace,
        workspace_mode=args.workspace_mode,
        model=args.model,
    )
    app = application(
        Service(
            store,
            token_file.read_text().strip(),
            runtime,
            push_configured=bool(args.firebase_credentials),
        )
    )

    async def lifecycle(app):
        push = None
        try:
            await runtime.start()
            if args.firebase_credentials:
                from .push import Push

                push = Push(store, args.firebase_credentials, args.firebase_project)
                push.start()
            yield
        finally:
            if push:
                await push.close()
            await runtime.close()
            store.close()

    app.cleanup_ctx.append(lifecycle)
    print(f"Hollywood room: http://127.0.0.1:{args.port}; client token file: {token_file}")
    web.run_app(app, host="127.0.0.1", port=args.port, access_log=None)


if __name__ == "__main__":
    main()
