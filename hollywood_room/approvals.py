"""Live upstream approvals. Durable records never resurrect an expired request."""

import asyncio
import json

from .store import Conflict


class Approvals:
    def __init__(self, store):
        self.store, self.pending = store, {}
        with store.db:
            store.db.execute(
                "UPDATE attention SET state='expired' WHERE id IN (SELECT id FROM event WHERE kind='approval') AND state='open'"
            )

    async def request(self, method, params, *, task, agent, visibility):
        supported = ("item/commandExecution/requestApproval", "item/fileChange/requestApproval")
        if method not in supported:
            raise ValueError(f"Unsupported server request: {method}")
        items = self.store.rows(
            "SELECT data FROM event WHERE kind='artifact' AND json_extract(data,'$.item.id')=? AND json_extract(data,'$.threadId')=? ORDER BY id DESC LIMIT 1",
            (params.get("itemId"), params.get("threadId")),
        )
        item = json.loads(items[0]["data"])["item"] if items else None
        command = params.get("command")
        can_accept = bool(command) if method == supported[0] else bool(item and item.get("changes"))
        text = (
            f"Command: {command}\nDirectory: {params.get('cwd', '')}\nReason: {params.get('reason', '')}"
            if command
            else json.dumps(
                item
                or {
                    "error": "Changes unavailable; approval disabled",
                    "reason": params.get("reason"),
                },
                indent=2,
            )
        )
        event = self.store.attention(
            task, agent, text, {"method": method, "request": params, "canAccept": can_accept}
        )
        with self.store.db:
            self.store.db.execute(
                "UPDATE event SET kind='approval',visibility=? WHERE id=?", (visibility, event)
            )
        future = asyncio.get_running_loop().create_future()
        self.pending[event] = future
        try:
            return await future
        finally:
            self.pending.pop(event, None)

    def answer(self, event_id, decision):
        if decision not in ("accept", "decline"):
            raise ValueError("Choose accept or decline for this specific request")
        future = self.pending.get(event_id)
        if future is None or future.done():
            raise Conflict("This approval is no longer pending in the current runtime")
        if decision == "accept" and not self.store.get_event(event_id)["data"]["canAccept"]:
            raise Conflict("The complete action is unavailable; this request cannot be approved")
        with self.store.db:
            self.store.db.execute(
                "UPDATE attention SET state='resolved',response=? WHERE id=?", (decision, event_id)
            )
            source = self.store.get_event(event_id)
            self.store.event(
                "resolved",
                source["task"],
                "you",
                "Approval response submitted",
                {"attention": event_id},
                visibility=source["visibility"],
            )
        future.set_result({"decision": decision})
        return {"attention": event_id, "decision": decision, "state": "responseSubmitted"}

    def expire(self, *, turn=None):
        for event_id, future in list(self.pending.items()):
            event = self.store.get_event(event_id)
            if turn and event["data"]["request"].get("turnId") != turn:
                continue
            if not future.done():
                future.set_result({"decision": "decline"})
            with self.store.db:
                self.store.db.execute(
                    "UPDATE attention SET state='expired' WHERE id=? AND state='open'", (event_id,)
                )
                self.store.event(
                    "resolved",
                    event["task"],
                    "hollywood",
                    "Approval expired",
                    {"attention": event_id},
                    visibility=event["visibility"],
                )
