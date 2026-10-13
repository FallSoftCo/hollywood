"""Hollywood owns execution connections; clients only observe and submit commands."""

import asyncio
import hashlib
import json
import logging
import time
from pathlib import Path

from jsonschema import Draft202012Validator, ValidationError

from .actions import perform
from .activity import action_text
from .approvals import Approvals
from .capacity import Capacity
from .conversations import Conversations
from .failures import context as failure_context
from .failures import summary as failure_summary
from .idle import Idle
from .limits import Limits
from .presence import agent_context
from .retries import delay as retry_delay
from .retries import disposition
from .rpc import AppServer, RpcError
from .tools import bounded, instruction, specs
from .workspace import SHARED_WORKSPACE_INSTRUCTIONS, Workspaces


class Runtime:
    def __init__(
        self,
        store,
        *,
        binary,
        home,
        state,
        workspace,
        model="gpt-5.5",
        reasoning_effort="low",
        ephemeral=False,
        workspace_mode="worktree",
        max_turns=None,
        memory_per_turn_mb=1024,
        reserve_memory_mb=512,
    ):
        self.store, self.workspace = store, Path(workspace).resolve()
        self.capacity = Capacity(
            max_turns=max_turns,
            memory_per_turn_mb=memory_per_turn_mb,
            reserve_memory_mb=reserve_memory_mb,
        )
        self.approvals = Approvals(store)
        self.model, self.ephemeral = model or "gpt-5.5", ephemeral
        self.reasoning_effort = reasoning_effort
        self.workspaces = Workspaces(
            self.workspace, Path(state) / "workspaces", mode=workspace_mode
        )
        self.rpc = AppServer(binary, Path(home), Path(state), self.event, self.tool)
        self.active = {}
        self.loaded = set()
        self.dispatchers = {}
        self.retiring = {}
        self.stopping = False
        self.ready = False
        self.poller = None
        self.recovery_poller = None
        self.auth_required = False
        self.conversations = Conversations(self)
        self.limits = Limits(self)
        self.limit_poller = None
        self.idle = Idle(store)
        self.validators = {
            tool["name"]: Draft202012Validator(tool["inputSchema"]) for tool in specs()
        }

    def capacity_status(self):
        active = len(set(self.dispatchers) | set(self.active))
        queued = self.store.db.execute(
            "SELECT count(*) FROM delivery WHERE state='queued'"
        ).fetchone()[0]
        status = self.capacity.snapshot(len(set(self.active) - set(self.dispatchers)), queued)
        status["active"] = active
        return status

    async def start(self):
        await self.rpc.start()
        account = await self.rpc.call("account/read", {"refreshToken": False})
        self.auth_required = bool(account.get("requiresOpenaiAuth") and not account.get("account"))
        self.ready = not self.auth_required
        self.poller = asyncio.create_task(self.poll())
        self.limit_poller = asyncio.create_task(self.poll_limits())
        self.recovery_poller = asyncio.create_task(self.poll_recovery())

    async def poll_recovery(self):
        reconnect_attempts = 0
        while not self.stopping:
            try:
                if not self.ready and not self.auth_required:
                    await self.rpc.close()
                    self.loaded.clear()
                    await self.rpc.start()
                    account = await self.rpc.call("account/read", {"refreshToken": False})
                    self.auth_required = bool(
                        account.get("requiresOpenaiAuth") and not account.get("account")
                    )
                    self.ready = not self.auth_required
                    reconnect_attempts = 0
                if self.ready:
                    await self.reconcile()
            except (RpcError, OSError, ValueError, asyncio.TimeoutError):
                reconnect_attempts += 1
                await asyncio.sleep(retry_delay(reconnect_attempts))
            await asyncio.sleep(1)

    async def reconcile(self):
        rows = self.store.rows(
            """SELECT d.*,b.thread FROM delivery d
            JOIN binding b ON b.task=d.task AND b.agent=d.agent
            WHERE d.state='uncertain' AND d.next_at<=? ORDER BY d.next_at,d.id LIMIT 1""",
            (time.time(),),
        )
        for delivery in rows:
            attempts = delivery["attempts"] + 1
            with self.store.db:
                self.store.db.execute(
                    "UPDATE delivery SET attempts=?,next_at=? WHERE id=?",
                    (attempts, time.time() + retry_delay(attempts), delivery["id"]),
                )
            # Tool-output turns do not persist clientUserMessageId in this protocol.
            # Keep them uncertain rather than treating missing evidence as rejection.
            if not delivery["thread"] or (
                not delivery["turn"] and delivery["authority"] not in ("user", "userReference")
            ):
                continue
            result = await self.rpc.call(
                "thread/read", {"threadId": delivery["thread"], "includeTurns": True}
            )
            for turn in result.get("thread", {}).get("turns", []):
                if turn.get("id") != delivery["turn"] and not any(
                    item.get("type") == "userMessage"
                    and item.get("clientId") == f"hollywood-{delivery['id']}"
                    for item in turn.get("items", [])
                ):
                    continue
                key = (delivery["task"], delivery["agent"])
                with self.store.db:
                    self.store.db.execute(
                        "UPDATE delivery SET turn=? WHERE id=?",
                        (turn["id"], delivery["id"]),
                    )
                if turn.get("status") == "inProgress":
                    event = self.store.get_event(delivery["event"])
                    self.active[key] = {
                        "thread": delivery["thread"],
                        "delivery": delivery,
                        "visibility": event["visibility"],
                    }
                    with self.store.db:
                        self.store.db.execute(
                            "UPDATE binding SET status='working',turn=? WHERE task=? AND agent=?",
                            (turn["id"], *key),
                        )
                    try:
                        await self.thread(*key)
                    except (RpcError, OSError, ValueError, asyncio.TimeoutError):
                        self.active.pop(key, None)
                        raise
                else:
                    with self.store.db:
                        self.store.db.execute(
                            "UPDATE binding SET status='idle',turn=NULL WHERE task=? AND agent=? AND turn=?",
                            (*key, turn["id"]),
                        )
                with self.store.db:
                    self.store.db.execute(
                        "UPDATE delivery SET state='confirmed',error=NULL,next_at=0 WHERE id=?",
                        (delivery["id"],),
                    )
                break

    async def poll_limits(self):
        while not self.stopping:
            try:
                if self.ready:
                    await self.limits.automatic()
                    current = self.limits.snapshot
                    if (
                        current
                        and time.time() - current["observedAt"] <= 30
                        and current["rates"].get("ordinaryUsageAllowed") is True
                    ):
                        with self.store.db:
                            self.store.db.execute(
                                "UPDATE delivery SET state='queued',next_at=0 WHERE state='waiting_usage'"
                            )
            except (RpcError, ValueError, OSError, asyncio.TimeoutError):
                logging.getLogger(__name__).warning(
                    "Account limit check unavailable; retrying after cooldown"
                )
            await asyncio.sleep(60)

    async def close(self):
        self.stopping = True
        if self.recovery_poller:
            self.recovery_poller.cancel()
            await asyncio.gather(self.recovery_poller, return_exceptions=True)
        if self.limit_poller:
            self.limit_poller.cancel()
            await asyncio.gather(self.limit_poller, return_exceptions=True)
        if self.poller:
            self.poller.cancel()
            await asyncio.gather(self.poller, return_exceptions=True)
        for retirement in self.retiring.values():
            retirement.cancel()
        await asyncio.gather(*self.retiring.values(), return_exceptions=True)
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
                rows = self.store.rows(
                    """SELECT * FROM (
                    SELECT d.*, row_number() OVER (
                        PARTITION BY d.task,d.agent
                        ORDER BY CASE d.authority WHEN 'user' THEN 0 ELSE 1 END,d.id
                    ) AS position
                    FROM delivery d JOIN binding b ON b.task=d.task AND b.agent=d.agent
                    WHERE d.state='queued' AND b.paused=0
                    AND NOT EXISTS (SELECT 1 FROM delivery pending
                        WHERE pending.task=d.task AND pending.agent=d.agent
                        AND pending.state IN ('uncertain','waiting_usage','blocked'))
                ) WHERE position=1 AND next_at<=?
                ORDER BY CASE authority WHEN 'user' THEN 0 ELSE 1 END,id""",
                    (time.time(),),
                )
                limit = self.capacity.snapshot(len(set(self.active) - set(self.dispatchers)))[
                    "limit"
                ]
                for delivery in rows:
                    key = (delivery["task"], delivery["agent"])
                    if key in self.retiring:
                        continue
                    if (
                        key not in self.active
                        and len(set(self.dispatchers) | set(self.active)) >= limit
                    ):
                        continue
                    if key not in self.dispatchers and (
                        key not in self.active or delivery["authority"] == "user"
                    ):
                        job = asyncio.create_task(self.dispatch(delivery))
                        self.dispatchers[key] = job
                        job.add_done_callback(lambda done, k=key: self.dispatchers.pop(k, None))
                self.idle.sample(set(self.active) | set(self.dispatchers))
            await asyncio.sleep(0.1)

    async def retire(self, key, thread):
        try:
            result = await self.rpc.call("thread/unsubscribe", {"threadId": thread})
            if result.get("status") in {"unsubscribed", "notLoaded", "notSubscribed"}:
                self.loaded.discard(thread)
        except (RpcError, asyncio.TimeoutError):
            # Older runtimes can lack unsubscribe; pressure still pauses new admissions.
            pass

    async def thread(self, task, agent):
        binding = self.store.rows("SELECT * FROM binding WHERE task=? AND agent=?", (task, agent))[
            0
        ]
        if binding["thread"] in self.loaded:
            return binding["thread"]
        cwd = await self.workspaces.for_agent(task, agent)
        instructions = instruction(
            agent, task, self.store.roster(self.store.task_team(task)), self.store.coordinator(task)
        )
        if self.workspaces.mode == "shared":
            instructions += SHARED_WORKSPACE_INSTRUCTIONS
        settings = {
            "cwd": str(cwd),
            "sandbox": "danger-full-access",
            "approvalPolicy": "never",
            "developerInstructions": instructions,
            "model": self.model,
            "config": {
                "features.multi_agent": False,
                "features.multi_agent_v2": False,
                "model_reasoning_effort": self.reasoning_effort,
            },
        }
        if binding["thread"]:
            await self.rpc.call(
                "thread/resume",
                {"threadId": binding["thread"], "excludeTurns": True, **settings},
            )
            self.loaded.add(binding["thread"])
            return binding["thread"]
        result = await self.rpc.call(
            "thread/start",
            {
                **settings,
                "ephemeral": self.ephemeral,
                "dynamicTools": specs(),
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
        phase = "preparingThread"
        event = self.store.get_event(delivery["event"])
        with self.store.db:
            self.store.db.execute(
                "UPDATE delivery SET state='dispatching',phase='preparingThread' WHERE id=?",
                (delivery["id"],),
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
                phase = "steeringTurn"
                with self.store.db:
                    self.store.db.execute(
                        "UPDATE delivery SET phase=? WHERE id=?", (phase, delivery["id"])
                    )
                await self.rpc.call(
                    "turn/steer",
                    {
                        "threadId": binding["thread"],
                        "expectedTurnId": binding["turn"],
                        "input": [{"type": "text", "text": event["body"]}],
                        "clientUserMessageId": f"hollywood-{delivery['id']}",
                        "additionalContext": {
                            "devicePresence": {
                                "kind": "application",
                                "value": agent_context(self.store),
                            }
                        },
                    },
                )
                with self.store.db:
                    self.store.db.execute(
                        "UPDATE delivery SET state='confirmed',turn=?,error=NULL,next_at=0 WHERE id=?",
                        (binding["turn"], delivery["id"]),
                    )
                return
            if delivery["authority"] == "user":
                with self.store.db:
                    self.store.db.execute(
                        "UPDATE binding SET turns=0 WHERE task=? AND agent=?", key
                    )
            # Admission is governed by host capacity in poll(); conversation age and
            # delegation depth do not make a queued message a delivery failure.
            thread = await self.thread(task, agent)
            # Explicit turn overrides also update already-loaded conversations.
            params = {
                "threadId": thread,
                "input": [],
                "model": self.model,
                "effort": self.reasoning_effort,
            }
            params["additionalContext"] = {
                "devicePresence": {"kind": "application", "value": agent_context(self.store)}
            }
            if delivery["authority"] in ("user", "userReference"):
                text = event["body"]
                if delivery["authority"] == "userReference":
                    recipients = ", ".join(
                        event["data"].get("targets", [self.store.coordinator(task)])
                    )
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
            phase = "startingTurn"
            with self.store.db:
                self.store.db.execute(
                    "UPDATE delivery SET phase=? WHERE id=?", (phase, delivery["id"])
                )
            result = await self.rpc.call("turn/start", params)
            turn = result["turn"]["id"]
            with self.store.db:
                self.store.db.execute(
                    "UPDATE delivery SET state='confirmed',turn=?,error=NULL,next_at=0 WHERE id=?",
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
            reason = str(exc).strip() or (
                "Timed out waiting for Codex"
                if isinstance(exc, asyncio.TimeoutError)
                else type(exc).__name__
            )
            if not steering:
                self.active.pop(key, None)
            state = disposition(exc, phase)
            attempts = delivery.get("attempts", 0) + 1
            next_at = time.time() + retry_delay(attempts)
            with self.store.db:
                self.store.db.execute(
                    "UPDATE delivery SET state=?,error=?,attempts=?,next_at=? WHERE id=?",
                    (state, reason[:500], attempts, next_at, delivery["id"]),
                )
            failure = failure_context(
                self.store,
                task=task,
                agent=agent,
                reason=reason[:400],
                phase=phase,
                error_type=type(exc).__name__,
                delivery={**delivery, "state": state},
                visibility=event["visibility"],
            )
            with self.store.db:
                self.store.event(
                    "deliveryFailure",
                    task,
                    agent,
                    failure_summary(failure),
                    {"deliveryFailure": failure},
                    visibility=event["visibility"],
                )

    async def event(self, method, params):
        if method == "account/updated":
            self.limits.generation += 1
            self.limits.snapshot = None
            return
        if method == "hollywood/runtimeLost":
            self.ready = False
            self.idle.disconnected()
            self.approvals.expire()
            with self.store.db:
                # Known turn IDs are acceptance receipts, retained for reconciliation.
                for active in self.active.values():
                    self.store.db.execute(
                        "UPDATE delivery SET state='uncertain',next_at=0 WHERE id=? AND state='confirmed'",
                        (active["delivery"]["id"],),
                    )
                self.store.db.execute(
                    "UPDATE binding SET status='unreachable' WHERE status='working'"
                )
            self.active.clear()
            return
        thread = params.get("threadId")
        matches = self.store.rows("SELECT * FROM binding WHERE thread=?", (thread,))
        if not matches:
            return
        binding = matches[0]
        key = (binding["task"], binding["agent"])
        active = self.active.get(key, {})
        if method in ("item/started", "item/completed"):
            item = params.get("item", {})
            text = action_text(item, method == "item/completed")
            if text:
                with self.store.db:
                    self.store.event(
                        "artifact",
                        *key,
                        text,
                        {
                            "thread": thread,
                            "itemId": item.get("id"),
                            "status": item.get("status"),
                            "completed": method == "item/completed",
                        },
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
            if thread in self.loaded and not self.ephemeral:
                retirement = asyncio.create_task(self.retire(key, thread))
                self.retiring[key] = retirement
                retirement.add_done_callback(lambda done, k=key: self.retiring.pop(k, None))
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
