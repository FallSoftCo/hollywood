"""Validated team tool actions, separate from transport and turn lifecycle."""

import re

from .models import TEAM
from .tools import bounded


async def perform(runtime, key, active, tool, args, call_id):
    task, agent = key
    depth = active["delivery"]["depth"] + 1
    if tool == "hollywood_say":
        target = args["to"]
        target_task = args["task"]
        if target not in ("room", *TEAM) or target == agent:
            raise ValueError("Unknown recipient or self-address")
        if active["visibility"] != "room" and (target != "room" or target_task != task):
            raise ValueError("Use hollywood_brief to share a private task change")
        targets = [target] if args["response"] == "required" and target != "room" else []
        event = runtime.store.message(
            call_id,
            task=target_task,
            author=agent,
            body=bounded(args["body"], 700),
            targets=targets,
            authority="tool",
            visibility=active["visibility"],
            depth=depth,
        )
        active["published"] = True
        return {"published": event["id"], "targets": targets}
    if tool == "hollywood_assign":
        if active["visibility"] != "room":
            raise ValueError(
                "Use hollywood_brief to share only a summary before delegating private work"
            )
        target, target_task = args["agent"], args["task"]
        if target not in TEAM or not re.fullmatch(r"[a-z0-9_-]{1,64}", target_task):
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
            assignment={"source": source_id, "title": args["title"]},
        )
        return {"assigned": target, "task": target_task, "event": event["id"]}
    if tool == "hollywood_task":
        action = args["action"]
        if action == "status":
            return runtime.store.snapshot()
        if action not in ("working", "blocked", "completed"):
            raise ValueError("Unknown task state")
        with runtime.store.db:
            runtime.store.db.execute("UPDATE task SET status=? WHERE id=?", (action, task))
            event = runtime.store.event(
                "task",
                task,
                agent,
                bounded(args["summary"], 700),
                {"status": action},
                visibility=active["visibility"],
            )
            source = runtime.store.rows("SELECT source FROM task WHERE id=?", (task,))[0]["source"]
            if source and active["visibility"] == "room":
                parent_task = runtime.store.get_event(source)["task"]
                if parent_task != task:
                    runtime.store.queue(event, parent_task, "coordinator", "tool", depth=depth)
            if action == "completed":
                runtime.store.db.execute("INSERT INTO push_outbox(event) VALUES(?)", (event,))
        return {"task": task, "status": action}
    if tool == "hollywood_read_room":
        events = runtime.store.events(
            after=args["after"], task=args["task"], limit=1, visibility=f"direct:{agent}"
        )
        return {"events": events, "nextCursor": events[-1]["id"] if events else args["after"]}
    if tool == "hollywood_ask":
        return {
            "attention": runtime.store.attention(
                task, agent, bounded(args["question"], 700), visibility=active["visibility"]
            )
        }
    if tool == "hollywood_brief":
        return runtime.store.message(
            call_id,
            task=task,
            author=agent,
            body=bounded(args["summary"], 700),
            targets=["coordinator"],
            authority="tool",
            depth=depth,
        )
    if tool == "hollywood_control":
        return await runtime.control(
            args["task"], args["agent"], args["action"], command_id=call_id
        )
    raise ValueError("Unknown Hollywood tool")
