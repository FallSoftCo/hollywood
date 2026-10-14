"""Validated team tool actions, separate from transport and turn lifecycle."""

import hashlib
import json
import re

from .tools import bounded


async def perform(runtime, key, active, tool, args, call_id):
    task, agent = key
    team = runtime.store.task_team(task)
    roster = runtime.store.roster(team)
    coordinator = runtime.store.coordinator(task)
    target_task = args.get("task", task)
    existing = runtime.store.rows("SELECT team FROM task WHERE id=?", (target_task,))
    if existing and existing[0]["team"] != team:
        raise ValueError("Task does not belong to this team")
    if tool == "hollywood_control" and args["agent"] not in roster:
        raise ValueError("Unknown teammate")
    depth = active["delivery"]["depth"] + 1
    if tool == "hollywood_provision":
        if active["visibility"] != "room":
            raise ValueError("Provisioning requires a public room context")
        from .rooms import provision
        return provision(runtime.store, task, agent, **args)
    if tool == "hollywood_rooms":
        from .rooms import catalog
        page = catalog(runtime.store, team, cursor=args["cursor"], limit=1)
        return {"authorityId": page["authorityId"],
                "rooms": [{"roomId": r["id"], "task": r["task"], "title": bounded(r["title"], 80),
                           "purpose": bounded(r["purpose"], 160), "purposeTruncated": len(r["purpose"].encode("utf-8")) > 160,
                           "members": len(r["members"])} for r in page["rooms"]],
                "nextCursor": page["next_cursor"]}
    if tool == "hollywood_room_context":
        from .rooms import detail, synchronize
        synchronize(runtime.store)
        room_id = runtime.store.rows("SELECT id FROM room_catalog WHERE task=?", (task,))[0]["id"]
        room = detail(runtime.store, room_id, team)
        actor_id = runtime.store.rows("SELECT id FROM actor_catalog WHERE legacy_agent=?", (agent,))[0]["id"]
        return {"roomId": room_id, "actorId": actor_id, "task": task,
                "purpose": bounded(room["purpose"], 240), "purposeTruncated": len(room["purpose"].encode("utf-8")) > 240,
                "member": any(m["id"] == actor_id for m in room["members"]),
                "contextIsolation": "room-local", "membershipGrantsExecution": False}
    if tool == "hollywood_roster":
        page = runtime.store.roster_page(team, cursor=args["cursor"], limit=3)
        return {
            "members": [
                {"id": member, "role": bounded(role, 100)} for member, role in page["team"].items()
            ],
            "total": page["total"],
            "nextCursor": page["next_cursor"],
        }
    if tool == "hollywood_say":
        target = args["to"]
        target_task = args["task"]
        if target not in ("room", *roster) or target == agent:
            raise ValueError("Unknown recipient or self-address")
        if active["visibility"] != "room" and (target != "room" or target_task != task):
            raise ValueError("Use hollywood_brief to share a private task change")
        targets = [target] if args["response"] == "required" and target != "room" else []
        lobby = runtime.store.rows("SELECT lobby FROM team WHERE id=?", (team,))[0]["lobby"]
        if target_task == lobby and target == coordinator and agent != coordinator:
            # Global observes reports; peer publication cannot self-admit a
            # coordinator turn in the human coordination room.
            targets = []
        previous = runtime.store.rows("SELECT event FROM command WHERE id=?", (call_id,))
        if previous:
            targets = runtime.store.get_event(previous[0]["event"])["data"].get("targets", targets)
        event = runtime.store.message(
            call_id,
            task=target_task,
            author=agent,
            body=bounded(args["body"], 700),
            targets=targets,
            authority="tool",
            visibility=active["visibility"],
            depth=depth,
            reply_to=active["delivery"].get("event") if target_task == task else None,
        )
        active["published"] = True
        return {"published": event["id"], "targets": targets}
    if tool == "hollywood_assign":
        if active["visibility"] != "room":
            raise ValueError(
                "Use hollywood_brief to share only a summary before delegating private work"
            )
        target, target_task = args["agent"], args["task"]
        if target not in roster or not re.fullmatch(r"[a-z0-9_-]{1,64}", target_task):
            raise ValueError("Invalid task or agent")
        source = runtime.store.get_event(active["delivery"]["event"])
        source_id = (
            source["id"]
            if source["author"] == "you"
            else runtime.store.rows("SELECT source FROM task WHERE id=?", (task,))[0]["source"]
        )
        if source_id is None:
            raise ValueError("A human task request is required before delegation")
        event = runtime.store.message(
            call_id,
            task=target_task,
            author=agent,
            body=bounded(args["brief"], 700),
            targets=[target],
            authority="tool",
            depth=depth,
            assignment={"source": source_id, "title": args["title"], "team": team,
                        **({"dependencies": args["dependencies"]} if "dependencies" in args else {}),
                        **({"acceptance": args["acceptance"]} if "acceptance" in args else {})},
        )
        return {"assigned": target, "task": target_task, "event": event["id"],
                **({"admission": "held_dependencies"} if args.get("dependencies") else {})}
    if tool == "hollywood_task":
        action = args["action"]
        if action == "status":
            return {**runtime.store.snapshot(team), "team": roster}
        if action not in ("working", "blocked", "completed"):
            raise ValueError("Unknown task state")
        from .store import Conflict
        summary = bounded(args["summary"], 700)
        digest = hashlib.sha256(json.dumps(
            ["task", task, agent, action, summary, active["visibility"]]
        ).encode()).hexdigest()
        with runtime.store.db:
            previous = runtime.store.rows("SELECT * FROM command WHERE id=?", (call_id,))
            if previous:
                if previous[0]["digest"] != digest:
                    raise Conflict("Task command ID already used with different contents")
                return {"task": task, "status": action}
            runtime.store.db.execute("UPDATE task SET status=? WHERE id=?", (action, task))
            event = runtime.store.event(
                "task",
                task,
                agent,
                summary,
                {"status": action, "owningTask": task, "owner": agent},
                visibility=active["visibility"],
            )
            # Status belongs to its owning room. Reading a result does not admit
            # a new coordinator turn or mirror the worker into another context.
            if action == "completed":
                runtime.store.db.execute("INSERT INTO push_outbox(event) VALUES(?)", (event,))
            runtime.store.db.execute("INSERT INTO command VALUES(?,?,?)", (call_id, digest, event))
        return {"task": task, "status": action}
    if tool == "hollywood_read_room":
        events = runtime.store.events(
            after=args["after"], task=args["task"], limit=1, visibility=f"direct:{agent}", team=team
        )
        return {"events": events, "nextCursor": events[-1]["id"] if events else args["after"]}
    if tool == "hollywood_ask":
        return {
            "attention": runtime.store.attention(
                task, agent, bounded(args["question"], 700), visibility=active["visibility"],
                command_id=call_id,
            )
        }
    if tool == "hollywood_brief":
        previous = runtime.store.rows("SELECT event FROM command WHERE id=?", (call_id,))
        targets = runtime.store.get_event(previous[0]["event"])["data"].get("targets", []) if previous else []
        return runtime.store.message(
            call_id,
            task=task,
            author=agent,
            body=bounded(args["summary"], 700),
            targets=targets,
            authority="tool",
            depth=depth,
        )
    if tool == "hollywood_control":
        return await runtime.control(
            args["task"], args["agent"], args["action"], command_id=call_id
        )
    raise ValueError("Unknown Hollywood tool")
