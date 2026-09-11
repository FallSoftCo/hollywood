"""Hollywood owns execution connections; clients only observe and submit commands."""

import asyncio
import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator, ValidationError

from .actions import perform
from .approvals import Approvals
from .rpc import AppServer, RpcError
from .tools import bounded, instruction, specs
from .workspace import Workspaces


class Runtime:
    def __init__(self, store, *, binary, home, state, workspace, model=None, ephemeral=False):
        self.store, self.workspace = store, Path(workspace).resolve()
        self.approvals = Approvals(store)
        self.model, self.ephemeral = model, ephemeral
        self.workspaces = Workspaces(self.workspace, Path(state) / "workspaces")
        self.rpc = AppServer(binary, Path(home), Path(state), self.event, self.tool)
        self.active = {}
        self.loaded = set()
        self.dispatchers = {}
        self.stopping = False
        self.ready = False
        self.poller = None
        self.auth_required = False
        self.validators = {
            tool["name"]: Draft202012Validator(tool["inputSchema"]) for tool in specs()
        }

    async def start(self):
        await self.rpc.start()
        account = await self.rpc.call("account/read", {"refreshToken": False})
        self.auth_required = bool(account.get("requiresOpenaiAuth") and not account.get("account"))
        self.ready = not self.auth_required
        self.poller = asyncio.create_task(self.poll())

    async def close(self):
        self.stopping = True
        if self.poller:
            self.poller.cancel()
            await asyncio.gather(self.poller, return_exceptions=True)
        await self.rpc.close()
        await asyncio.gather(*self.dispatchers.values(), return_exceptions=True)

    async def poll(self):
        while not self.stopping:
            if self.auth_required:
                await asyncio.sleep(5)
                try:
                    account = await self.rpc.call("account/read", {"refreshToken": False})
                except RpcError:
                    self.auth_required = False
                    continue
                self.auth_required = not bool(account.get("account"))
                self.ready = not self.auth_required
            if self.ready:
                rows = self.store.rows("""SELECT d.* FROM delivery d JOIN binding b
                    ON b.task=d.task AND b.agent=d.agent WHERE d.state='queued' AND b.paused=0
                    ORDER BY CASE d.authority WHEN 'user' THEN 0 ELSE 1 END,d.id LIMIT 100""")
                for delivery in rows:
                    key = (delivery["task"], delivery["agent"])
                    if (
                        key not in self.active
                        and len(set(self.dispatchers) | set(self.active)) >= 4
                    ):
                        continue
                    if key not in self.dispatchers and (
                        key not in self.active or delivery["authority"] == "user"
                    ):
                        job = asyncio.create_task(self.dispatch(delivery))
                        self.dispatchers[key] = job
                        job.add_done_callback(lambda done, k=key: self.dispatchers.pop(k, None))
            await asyncio.sleep(0.1)

    async def thread(self, task, agent):
        binding = self.store.rows("SELECT * FROM binding WHERE task=? AND agent=?", (task, agent))[
            0
        ]
        if binding["thread"]:
            if binding["thread"] not in self.loaded:
                await self.rpc.call(
                    "thread/resume", {"threadId": binding["thread"], "includeTurns": False}
                )
                self.loaded.add(binding["thread"])
            return binding["thread"]
        cwd = await self.workspaces.for_agent(task, agent)
        result = await self.rpc.call(
            "thread/start",
            {
                "cwd": str(cwd),
                "model": self.model,
                "ephemeral": self.ephemeral,
                "sandbox": "workspace-write",
                "approvalPolicy": "on-request",
                "developerInstructions": instruction(agent, task),
                "dynamicTools": specs(),
                "config": {"features.multi_agent": False, "features.multi_agent_v2": False},
            },
        )
        thread = result["thread"]["id"]
        self.loaded.add(thread)
        with self.store.db:
            self.store.db.execute(
                "UPDATE binding SET thread=? WHERE task=? AND agent=?", (thread, task, agent)
            )
        return thread

    async def dispatch(self, delivery):
        task, agent = delivery["task"], delivery["agent"]
        key = (task, agent)
        steering = key in self.active
        event = self.store.get_event(delivery["event"])
        with self.store.db:
            self.store.db.execute(
                "UPDATE delivery SET state='dispatching' WHERE id=?", (delivery["id"],)
            )
        try:
            binding = self.store.rows("SELECT * FROM binding WHERE task=? AND agent=?", key)[0]
            if key in self.active and delivery["authority"] == "user" and binding["turn"]:
                if event["visibility"] != self.active[key]["visibility"]:
                    # Finish the current visibility scope before entering a private discussion.
                    with self.store.db:
                        self.store.db.execute(
                            "UPDATE delivery SET state='queued' WHERE id=?", (delivery["id"],)
                        )
                    return
                await self.rpc.call(
                    "turn/steer",
                    {
                        "threadId": binding["thread"],
                        "expectedTurnId": binding["turn"],
                        "input": [{"type": "text", "text": event["body"]}],
                        "clientUserMessageId": f"hollywood-{delivery['id']}",
                    },
                )
                with self.store.db:
                    self.store.db.execute(
                        "UPDATE delivery SET state='confirmed',turn=? WHERE id=?",
                        (binding["turn"], delivery["id"]),
                    )
                return
            if delivery["authority"] == "user":
                with self.store.db:
                    self.store.db.execute(
                        "UPDATE binding SET turns=0 WHERE task=? AND agent=?", key
                    )
            elif delivery["depth"] > 4 or binding["turns"] >= 40:
                raise ValueError("Automatic turn budget reached; human review required")
            thread = await self.thread(task, agent)
            params = {"threadId": thread, "input": []}
            if delivery["authority"] in ("user", "userReference"):
                text = event["body"]
                if delivery["authority"] == "userReference":
                    recipients = ", ".join(event["data"].get("targets", ["coordinator"]))
                    text = (
                        f"Forwarded human room request #{event['id']}, originally addressed to {recipients}. "
                        f"You are {agent}, receiving a reference to the authorized scope. Instructions "
                        "to the original recipients (including 'you' or 'yourself') remain addressed "
                        "to them. Do not re-delegate their assignment. Wait for your Hollywood brief; "
                        "reply [silent] to this context-only delivery. Original request:\n\n" + text
                    )
                params["input"] = [{"type": "text", "text": text}]
                params["clientUserMessageId"] = f"hollywood-{delivery['id']}"
            else:
                params["toolOutput"] = {
                    "name": "room_message",
                    "namespace": "hollywood",
                    "output": bounded(
                        {
                            "id": event["id"],
                            "author": event["author"],
                            "task": event["task"],
                            "kind": event["kind"],
                            "body": event["body"],
                        }
                    ),
                }
            self.active[key] = {
                "thread": thread,
                "delivery": delivery,
                "visibility": event["visibility"],
            }
            result = await self.rpc.call("turn/start", params)
            turn = result["turn"]["id"]
            with self.store.db:
                self.store.db.execute(
                    "UPDATE delivery SET state='confirmed',turn=? WHERE id=?",
                    (turn, delivery["id"]),
                )
                if key in self.active:
                    self.store.db.execute(
                        "UPDATE binding SET status='working',turn=? WHERE task=? AND agent=?",
                        (turn, *key),
                    )
                self.store.db.execute(
                    "UPDATE binding SET turns=turns+1 WHERE task=? AND agent=?", key
                )
        except (RpcError, ValueError, OSError, asyncio.TimeoutError) as exc:
            if not steering:
                self.active.pop(key, None)
            with self.store.db:
                self.store.db.execute(
                    "UPDATE delivery SET state='uncertain',error=? WHERE id=?",
                    (str(exc)[:500], delivery["id"]),
                )
            self.store.attention(
                task,
                agent,
                f"Could not confirm delivery: {str(exc)[:400]}",
                visibility=event["visibility"],
            )

    async def event(self, method, params):
        if method == "hollywood/runtimeLost":
            self.ready = False
            self.approvals.expire()
            with self.store.db:
                self.store.db.execute(
                    "UPDATE binding SET status='unreachable' WHERE status='working'"
                )
            return
        thread = params.get("threadId")
        matches = self.store.rows("SELECT * FROM binding WHERE thread=?", (thread,))
        if not matches:
            return
        binding = matches[0]
        key = (binding["task"], binding["agent"])
        active = self.active.get(key, {})
        if method in ("item/started", "item/completed") and params.get("item", {}).get("type") in (
            "fileChange",
            "commandExecution",
        ):
            with self.store.db:
                self.store.event(
                    "artifact",
                    *key,
                    params["item"]["type"],
                    params,
                    visibility=active.get("visibility", "room"),
                )
        if method == "item/completed" and params.get("item", {}).get("type") == "agentMessage":
            item = params["item"]
            text = item.get("text", "").strip()
            kind = (
                "message"
                if item.get("phase") != "commentary" and not active.get("published")
                else "agentNote"
            )
            if text and text != "[silent]":
                with self.store.db:
                    self.store.event(
                        kind,
                        *key,
                        text[:12000],
                        {"thread": thread, "item": item["id"], "phase": item.get("phase")},
                        visibility=active.get("visibility", "room"),
                    )
        if method == "turn/completed":
            turn = params["turn"]
            self.approvals.expire(turn=turn["id"])
            with self.store.db:
                self.store.db.execute(
                    "UPDATE binding SET status=?,turn=NULL WHERE task=? AND agent=?",
                    ("idle" if turn["status"] == "completed" else turn["status"], *key),
                )
                self.store.event(
                    "turn",
                    *key,
                    turn["status"],
                    {"turn": turn["id"], "error": turn.get("error")},
                    visibility=active.get("visibility", "room"),
                )
            self.active.pop(key, None)
            if turn["status"] == "failed":
                self.store.attention(
                    *key,
                    "Agent turn failed",
                    {"error": turn.get("error")},
                    visibility=active.get("visibility", "room"),
                )

    async def tool(self, method, params):
        binding = self.store.rows("SELECT * FROM binding WHERE thread=?", (params["threadId"],))[0]
        key = (binding["task"], binding["agent"])
        active = self.active[key]
        if method != "item/tool/call":
            return await self.approvals.request(
                method, params, task=key[0], agent=key[1], visibility=active["visibility"]
            )
        try:
            self.validators[params["tool"]].validate(params["arguments"])
            result = await perform(
                self,
                key,
                active,
                params["tool"],
                params["arguments"],
                f"tool:{params['threadId']}:{params['callId']}",
            )
            return {
                "success": True,
                "contentItems": [{"type": "inputText", "text": bounded(result)}],
            }
        except (ValueError, KeyError, ValidationError) as exc:
            return {
                "success": False,
                "contentItems": [{"type": "inputText", "text": bounded(str(exc))}],
            }

    async def control(self, task, agent, action, *, command_id):
        key = (task, agent)
        if action not in ("pause", "resume", "interrupt"):
            raise ValueError("Unknown control action")
        digest = hashlib.sha256(json.dumps([task, agent, action]).encode()).hexdigest()
        existing = self.store.rows("SELECT * FROM command WHERE id=?", (command_id,))
        if existing:
            if existing[0]["digest"] != digest:
                raise ValueError("Control ID already used with different contents")
            return self.store.get_event(existing[0]["event"])["data"]
        rows = self.store.rows("SELECT * FROM binding WHERE task=? AND agent=?", key)
        if not rows:
            raise ValueError("No agent conversation in this task")
        result = {
            "agent": agent,
            "task": task,
            "action": action,
            "turn": rows[0]["turn"],
            "state": "pending" if action == "interrupt" and rows[0]["turn"] else "confirmed",
        }
        with self.store.db:
            self.store.db.execute(
                "UPDATE binding SET paused=? WHERE task=? AND agent=?", (action != "resume", *key)
            )
            event = self.store.event("control", task, "hollywood", f"{action}: {agent}", result)
            self.store.db.execute("INSERT INTO command VALUES(?,?,?)", (command_id, digest, event))
        if result["state"] == "pending":
            try:
                await self.rpc.call(
                    "turn/interrupt", {"threadId": rows[0]["thread"], "turnId": result["turn"]}
                )
                result["state"] = "interruptRequested"
            except (RpcError, asyncio.TimeoutError):
                result["state"] = "uncertain"
            with self.store.db:
                self.store.db.execute(
                    "UPDATE event SET data=? WHERE id=?", (json.dumps(result), event)
                )
        return result
