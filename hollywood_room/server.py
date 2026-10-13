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

from .external_threads import ExternalThreads
from .failures import present_event
from .history import HistoryQuery, history_page
from .models import (
    Answer,
    ApprovalAnswer,
    Control,
    ConversationControl,
    ConversationCreate,
    Device,
    Dismiss,
    ExternalAssociation,
    LimitReset,
    Message,
    TaskCreate,
    TeamCreate,
    TeammateCreate,
)
from .presence import PresenceReport
from .presence import report as report_presence
from .rpc import RpcError
from .runtime import Runtime
from .store import Conflict, Store
from .updates import Updates

SERVICE = web.AppKey("service", object)


class Service:
    def __init__(self, store, token, runtime=None, *, push_configured=False, update_directory=None):
        self.store, self.token, self.runtime = store, token, runtime
        self.push_configured = push_configured
        self.updates = Updates(update_directory)

    def post(self, message, *, resolves=None, team="default"):
        roster = self.store.roster(team)
        if not message.body.strip() or len(message.body.encode("utf-8")) > 6000:
            raise ValueError("Messages must contain text and fit within 6000 UTF-8 bytes")
        task, targets = message.task, message.targets
        if task == "lobby" and team != "default":
            task = self.store.rows("SELECT lobby FROM team WHERE id=?", (team,))[0]["lobby"]
        if message.replyTo:
            source = self.store.get_event(message.replyTo)
            task = source["task"]
            self.check_task(task, team)
            targets = targets or (
                [source["author"]]
                if source["author"] in roster
                else source["data"].get("targets", [])
            )
        if not targets:
            match = re.match(r"^@?([A-Za-z][\w-]*)\s*[:,]", message.body)
            mention = re.match(r"^@([A-Za-z][\w-]*)\b", message.body)
            named = match or mention
            if named and (mention or named[1].lower() in roster):
                targets = [named[1].lower()]
            else:
                targets = [self.store.coordinator(task)]
        self.check_task(task, team)
        if any(target not in roster for target in targets):
            raise ValueError("Unknown recipient. Available: " + ", ".join(roster))
        targets = list(dict.fromkeys(targets))
        if message.visibility == "direct" and len(targets) != 1:
            raise ValueError("A direct conversation requires exactly one teammate")
        visibility = f"direct:{targets[0]}" if message.visibility == "direct" else "room"
        if message.replyTo and source["visibility"] != "room":
            visibility = source["visibility"]
            if targets != [visibility.split(":", 1)[1]]:
                raise ValueError("A private reply cannot change recipients")
        if (
            resolves is None
            and message.replyTo
            and source["kind"] == "attention"
            and source["author"] in targets
            and self.store.rows(
                "SELECT id FROM attention WHERE id=? AND state='open'", (source["id"],)
            )
        ):
            resolves = source["id"]
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

    def check_task(self, task, team):
        if self.store.task_team(task) != team:
            raise ValueError("Task does not belong to this team")

    def check_event(self, event_id, team):
        event = self.store.get_event(event_id)
        self.check_task(event["task"], team)
        return event


@web.middleware
async def boundary(request, handler):
    service = request.app[SERVICE]
    if request.path != "/health" and not hmac.compare_digest(
        request.headers.get("Authorization", ""), f"Bearer {service.token}"
    ):
        raise web.HTTPUnauthorized()
    try:
        team = request.match_info.get("team")
        if team and not service.store.rows("SELECT id FROM team WHERE id=?", (team,)):
            raise ValueError("Unknown team")
        return await handler(request)
    except Conflict as exc:
        return web.json_response({"error": str(exc)}, status=409)
    except (RpcError, asyncio.TimeoutError) as exc:
        return web.json_response({"error": "Runtime request unconfirmed; retry the original command", "detail": str(exc)[:200]}, status=503)
    except (ValueError, ValidationError, KeyError, IndexError) as exc:
        return web.json_response({"error": str(exc)[:1000]}, status=400)


def application(service):
    app = web.Application(middlewares=[boundary], client_max_size=16 * 1024)
    app[SERVICE] = service

    def selected_team(request):
        team = request.match_info.get("team", "default")
        if not service.store.rows("SELECT id FROM team WHERE id=?", (team,)):
            raise ValueError("Unknown team")
        return team

    def scoped_task(request):
        task = request.query.get("task")
        if task:
            service.check_task(task, selected_team(request))
        return task

    async def health(request):
        return web.json_response(
            {
                "service": "hollywood-room",
                "apiVersion": 2,
                "runtimeReady": bool(service.runtime and service.runtime.ready),
            }
        )

    async def snapshot(request):
        roster = service.store.roster_page(selected_team(request))
        teams = service.store.team_page()
        return web.json_response(
            {
                **service.store.snapshot(selected_team(request)),
                "team": roster["team"],
                "teamTotal": roster["total"],
                "teamNextCursor": roster["next_cursor"],
                "teams": teams["teams"],
                "teamsNextCursor": teams["next_cursor"],
                "teamId": selected_team(request),
                "capabilities": {
                    "conversations": bool(service.runtime),
                    "earnedCreditReset": bool(service.runtime),
                    "attentionDismissal": True,
                    "attentionCardDismissal": True,
                    "deliveryFailureContext": True,
                },
                "lobby": service.store.rows(
                    "SELECT lobby FROM team WHERE id=?", (selected_team(request),)
                )[0]["lobby"],
                "capacity": service.runtime.capacity_status()
                if service.runtime
                else {
                    "limit": 0,
                    "active": 0,
                    "queued": service.store.db.execute(
                        "SELECT count(*) FROM delivery WHERE state='queued'"
                    ).fetchone()[0],
                    "reason": "runtime unavailable",
                },
                "runtimeReady": bool(service.runtime and service.runtime.ready),
                "pushConfigured": service.push_configured,
            }
        )

    async def messages(request):
        batch = service.store.events(
            after=int(request.query.get("after", 0)),
            task=scoped_task(request),
            team=selected_team(request),
            limit=int(request.query.get("limit", 100)),
        )
        return web.json_response({"data": [present_event(service.store, event) for event in batch]})

    async def post(request):
        return web.json_response(
            service.post(Message.model_validate(await request.json()), team=selected_team(request)),
            status=202,
        )

    async def history(request):
        return web.json_response(
            history_page(
                service.store,
                HistoryQuery.model_validate(dict(request.query)),
                team=selected_team(request),
            )
        )

    async def detail(request):
        event_id = int(request.match_info["id"])
        event = present_event(service.store, service.check_event(event_id, selected_team(request)))
        card = service.store.attention_card_state(event)
        return web.json_response({"event": {**event, **card}, "attentionState": None, **card})

    async def receipt(request):
        rows = service.store.rows("SELECT * FROM command WHERE id=?", (request.match_info["id"],))
        if not rows:
            raise web.HTTPNotFound()
        event = rows[0]["event"]
        service.check_event(event, selected_team(request))
        return web.json_response(
            {
                "event": service.store.get_event(event),
                "deliveries": service.store.rows("SELECT * FROM delivery WHERE event=?", (event,)),
            }
        )

    async def control(request):
        command = Control.model_validate(await request.json())
        service.check_task(command.task, selected_team(request))
        if command.agent not in service.store.roster(selected_team(request)):
            raise ValueError("Unknown teammate")
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
        service.check_event(item_id, selected_team(request))
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
            team=selected_team(request),
        )
        return web.json_response(result)

    async def dismiss(request):
        value = Dismiss.model_validate(await request.json())
        return web.json_response(
            service.store.dismiss_attention(
                value.commandId, int(request.match_info["id"]), selected_team(request)
            )
        )

    async def approval(request):
        value = ApprovalAnswer.model_validate(await request.json())
        service.check_event(int(request.match_info["id"]), selected_team(request))
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
                batch = service.store.events(after=cursor, team=selected_team(request))
                if batch:
                    for event in batch:
                        await response.write(
                            f"id: {event['id']}\ndata: {json.dumps(present_event(service.store, event))}\n\n".encode()
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

    async def device_presence(request):
        value = PresenceReport.model_validate(await request.json())
        return web.json_response(report_presence(service.store, request.match_info["id"], value))

    async def revoke(request):
        with service.store.db:
            service.store.db.execute(
                "UPDATE device SET revoked=1 WHERE id=?", (request.match_info["id"],)
            )
            service.store.db.execute(
                "DELETE FROM device_presence WHERE device=?", (request.match_info["id"],)
            )
        return web.json_response({"revoked": True})

    def page_args(request):
        return {
            "cursor": request.query.get("cursor", ""),
            "limit": min(max(int(request.query.get("limit", 100)), 1), 100),
        }

    async def actors(request):
        from .rooms import actors as actor_catalog
        return web.json_response(actor_catalog(service.store, selected_team(request), **page_args(request)))

    async def rooms(request):
        from .rooms import catalog
        return web.json_response(catalog(service.store, selected_team(request), task=request.query.get("task"), **page_args(request)))

    async def room_create(request):
        from .models import RoomCreate
        from .rooms import create
        value = RoomCreate.model_validate(await request.json())
        return web.json_response(create(service.store, selected_team(request),
            value.task, value.title, value.purpose), status=201)

    async def room_detail(request):
        from .rooms import detail
        return web.json_response(detail(service.store, request.match_info['room'], selected_team(request)))

    async def room_purpose(request):
        from .rooms import set_purpose
        value = await request.json()
        if not isinstance(value, dict) or type(value.get('revision')) is not int:
            raise ValueError('Expected integer room revision')
        return web.json_response(set_purpose(service.store, request.match_info['room'],
            selected_team(request), value.get('purpose'), value['revision']))

    async def room_join(request):
        from .rooms import join
        return web.json_response(join(service.store, request.match_info['room'],
            selected_team(request), request.match_info['actor']))

    async def teams(request):
        return web.json_response(service.store.team_page(**page_args(request)))

    async def teammates(request):
        return web.json_response(
            service.store.roster_page(selected_team(request), **page_args(request))
        )

    async def create_team(request):
        value = TeamCreate.model_validate(await request.json())
        return web.json_response(
            service.store.add_team(value.id, value.name, value.coordinator, value.role), status=201
        )

    async def create_teammate(request):
        value = TeammateCreate.model_validate(await request.json())
        return web.json_response(
            service.store.add_teammate(value.id, selected_team(request), value.role), status=201
        )

    async def pending(request):
        team = selected_team(request) if "team" in request.match_info else None
        after = int(request.query.get("cursor", request.query.get("after", 0)))
        limit = min(max(int(request.query.get("limit", 100)), 1), 100)
        scope, args = (" AND t.team=?", [team]) if team else ("", [])
        rows = service.store.rows(
            "SELECT a.id,t.team AS team_id,t.id AS task_id,t.title AS task_title,g.name AS team_name "
            "FROM attention a JOIN task t ON t.id=a.task JOIN team g ON g.id=t.team "
            "WHERE a.state='open'" + scope + " AND a.id>? ORDER BY a.id LIMIT ?",
            [*args, after, limit + 1],
        )
        data = [
            {
                **present_event(service.store, service.store.get_event(row["id"])),
                **row,
                "attentionState": "open",
            }
            for row in rows[:limit]
        ]
        total = service.store.db.execute(
            "SELECT count(*) FROM attention a JOIN task t ON t.id=a.task WHERE a.state='open'"
            + scope,
            args,
        ).fetchone()[0]
        cursor = data[-1]["id"] if len(rows) > limit else None
        return web.json_response(
            {
                "data": data,
                "total_pending": total,
                "next_cursor": cursor,
                "total": total,
                "nextCursor": cursor,
            }
        )

    async def create_task(request):
        value = TaskCreate.model_validate(await request.json())
        team = selected_team(request)
        if (
            not value.title.strip()
            or not value.body.strip()
            or len(value.body.encode("utf-8")) > 6000
        ):
            raise ValueError("Task title/body must contain text; body must fit 6000 UTF-8 bytes")
        targets = list(dict.fromkeys(value.targets)) or [
            service.store.rows("SELECT coordinator FROM team WHERE id=?", (team,))[0]["coordinator"]
        ]
        if any(target not in service.store.roster(team) for target in targets):
            raise ValueError("Unknown teammate")
        existing = service.store.rows("SELECT id FROM task WHERE id=?", (value.id,))
        if existing and not service.store.rows(
            "SELECT id FROM command WHERE id=?", (value.commandId,)
        ):
            raise Conflict("Task ID already exists")
        if existing:
            service.check_task(value.id, team)
        return web.json_response(
            service.store.message(
                value.commandId,
                task=value.id,
                author="you",
                body=value.body,
                targets=targets,
                authority="user",
                assignment={"title": value.title, "source": None, "team": team},
            ),
            status=202,
        )

    async def update_manifest(request):
        try:
            result = await asyncio.to_thread(service.updates.manifest)
        except (OSError, ValueError):
            raise web.HTTPServiceUnavailable(text="Published update is unavailable; retry later")
        return web.json_response(result, headers={"Cache-Control": "private, no-store"})

    async def update_apk(request):
        try:
            result = await asyncio.to_thread(service.updates.release, request.match_info["digest"])
        except FileNotFoundError:
            raise web.HTTPNotFound()
        except (OSError, ValueError):
            raise web.HTTPServiceUnavailable(text="Published update failed integrity verification")
        if result is None:
            raise web.HTTPNotFound()
        release, path = result
        return web.FileResponse(
            path,
            headers={
                "Content-Type": "application/vnd.android.package-archive",
                "Content-Disposition": f'attachment; filename="losangelex-{release.versionCode}.apk"',
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    def lifecycle_runtime():
        if not service.runtime:
            raise web.HTTPServiceUnavailable(text="Runtime unavailable")
        return service.runtime

    async def conversations(request):
        runtime = lifecycle_runtime()
        return web.json_response(runtime.conversations.list(
            request.query["task"], request.query["agent"], selected_team(request),
            request.query.get("cursor", ""), int(request.query.get("limit", 50))))

    async def external_threads(request):
        return web.json_response(await ExternalThreads(lifecycle_runtime()).list(
            request.query["task"], request.query["agent"], selected_team(request),
            request.query.get("cursor"), int(request.query.get("limit", 25))))

    async def external_thread(request):
        return web.json_response(await ExternalThreads(lifecycle_runtime()).read(
            request.match_info["id"], request.query["task"], request.query["agent"], selected_team(request)))

    async def external_association(request):
        command = ExternalAssociation.model_validate(await request.json())
        result = await ExternalThreads(lifecycle_runtime()).associate(
            request.match_info["id"], command.commandId, command.expectedOwnerId,
            command.task, command.agent, selected_team(request), command.action)
        return web.json_response(result)

    async def external_associations(request):
        return web.json_response(ExternalThreads(lifecycle_runtime()).associations(
            request.query["task"], request.query["agent"], selected_team(request),
            request.query.get("cursor", ""), int(request.query.get("limit", 25))))

    async def conversation_create(request):
        command = ConversationCreate.model_validate(await request.json())
        result = await lifecycle_runtime().conversations.command(
            command.commandId, "new", selected_team(request), task=command.task,
            agent=command.agent, label=command.label)
        return web.json_response(result)

    async def conversation_control(request):
        command = ConversationControl.model_validate(await request.json())
        result = await lifecycle_runtime().conversations.command(
            command.commandId, command.action, selected_team(request), identifier=request.match_info["id"])
        return web.json_response(result)

    async def limits(request):
        selected_team(request)
        return web.json_response(await lifecycle_runtime().limits.read())

    async def limit_reset(request):
        command = LimitReset.model_validate(await request.json())
        runtime = lifecycle_runtime()
        if command.conversationId:
            runtime.conversations.get(command.conversationId, selected_team(request))
        result = await runtime.limits.reset(command.commandId, expected_account_key=command.expectedAccountKey)
        return web.json_response(result)

    routes = [
        web.get("/hollywood/v2/actors", actors),
        web.get("/hollywood/v2/rooms", rooms),
        web.post("/hollywood/v2/rooms", room_create),
        web.get("/hollywood/v2/rooms/{room}", room_detail),
        web.put("/hollywood/v2/rooms/{room}/purpose", room_purpose),
        web.put("/hollywood/v2/rooms/{room}/members/{actor}", room_join),
        web.get("/hollywood/v2/external-threads", external_threads),
        web.get("/hollywood/v2/external-threads/{id}", external_thread),
        web.post("/hollywood/v2/external-threads/{id}/association", external_association),
        web.get("/hollywood/v2/external-associations", external_associations),
        web.get("/hollywood/v2/conversations", conversations),
        web.post("/hollywood/v2/conversations", conversation_create),
        web.post("/hollywood/v2/conversations/{id}/control", conversation_control),
        web.get("/hollywood/v2/limits", limits),
        web.post("/hollywood/v2/limits/reset", limit_reset),
        web.get("/health", health),
        web.get("/hollywood/v2/overview", snapshot),
        web.post("/hollywood/v2/tasks", create_task),
        web.get("/hollywood/v2/messages", messages),
        web.get("/hollywood/v2/history", history),
        web.post("/hollywood/v2/messages", post),
        web.get("/hollywood/v2/messages/{id}", detail),
        web.get("/hollywood/v2/events", events),
        web.get("/hollywood/v2/commands/{id}", receipt),
        web.post("/hollywood/v2/control", control),
        web.post("/hollywood/v2/attention/{id}/answer", answer),
        web.post("/hollywood/v2/attention/{id}/dismiss", dismiss),
        web.post("/hollywood/v2/devices", device),
        web.post("/hollywood/v2/devices/{id}/presence", device_presence),
        web.delete("/hollywood/v2/devices/{id}", revoke),
        web.post("/hollywood/v2/approval/{id}/answer", approval),
    ]
    app.add_routes(routes)
    # Same handlers under explicit team scope; old clients retain the default team.
    app.add_routes(
        [
            web.route(
                route.method,
                "/hollywood/v2/teams/{team}" + route.path.removeprefix("/hollywood/v2"),
                route.handler,
            )
            for route in routes
            if route.path.startswith("/hollywood/v2/")
        ]
    )
    app.add_routes(
        [
            web.get("/hollywood/v2/update", update_manifest),
            web.get("/hollywood/v2/update/apk/{digest}", update_apk),
            web.get("/hollywood/v2/teams", teams),
            web.post("/hollywood/v2/teams", create_team),
            web.post("/hollywood/v2/teams/{team}/teammates", create_teammate),
            web.get("/hollywood/v2/teams/{team}/teammates", teammates),
            web.get("/hollywood/v2/attention", pending),
            web.get("/hollywood/v2/teams/{team}/attention", pending),
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
    parser.add_argument("--model", default="gpt-5.5")
    parser.add_argument("--reasoning-effort", default="low",
                        choices=("none", "minimal", "low", "medium", "high", "xhigh", "max"))
    parser.add_argument(
        "--automatic-credit-reset", action=argparse.BooleanOptionalAction, default=True,
        help="Automatically redeem earned reset credits when usage is exhausted",
    )
    parser.add_argument(
        "--max-turns", type=int, help="Optional running-turn ceiling; otherwise resource-derived"
    )
    parser.add_argument("--memory-per-turn-mb", type=int, default=1024)
    parser.add_argument("--reserve-memory-mb", type=int, default=512)
    parser.add_argument("--port", type=int, default=18766)
    parser.add_argument("--firebase-credentials", type=Path)
    parser.add_argument("--firebase-project")
    parser.add_argument(
        "--android-updates",
        type=Path,
        help="Published Android APK directory (default: STATE/android-updates)",
    )
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
        reasoning_effort=args.reasoning_effort,
        automatic_credit_reset=args.automatic_credit_reset,
        max_turns=args.max_turns,
        memory_per_turn_mb=args.memory_per_turn_mb,
        reserve_memory_mb=args.reserve_memory_mb,
    )
    app = application(
        Service(
            store,
            token_file.read_text().strip(),
            runtime,
            push_configured=bool(args.firebase_credentials),
            update_directory=args.android_updates or state / "android-updates",
        )
    )

    async def lifecycle(app):
        push = None
        try:
            await runtime.start()
            if args.firebase_credentials:
                from .push import Push

                push = Push(store, args.firebase_credentials, args.firebase_project,
                            updates=app[SERVICE].updates)
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
