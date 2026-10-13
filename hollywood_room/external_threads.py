"""Owner-scoped metadata discovery and associations, never execution ownership."""

import hashlib
import json
import socket
import uuid

from .store import Conflict


class ExternalThreads:
    def __init__(self, runtime):
        self.runtime = runtime
        # Stable across process restarts/upgrades; distinct configured homes/hosts
        # never imply shared native writer ownership.
        identity = [socket.gethostname(), str(runtime.rpc.home.resolve()),
                    str((runtime.rpc.state / "sqlite").resolve()), "stdio"]
        self.owner_id = hashlib.sha256(json.dumps(identity).encode()).hexdigest()
        runtime.store.db.executescript("""
            CREATE TABLE IF NOT EXISTS external_association (
                owner TEXT NOT NULL, thread TEXT NOT NULL,
                task TEXT NOT NULL REFERENCES task(id),
                agent TEXT NOT NULL REFERENCES teammate(id),
                PRIMARY KEY(owner,thread)
            );
            CREATE TABLE IF NOT EXISTS external_command (
                id TEXT PRIMARY KEY, payload TEXT NOT NULL, result TEXT NOT NULL
            );
        """)

    def scope(self, task, agent, team):
        if self.runtime.store.task_team(task) != team or agent not in self.runtime.store.roster(team):
            raise ValueError("Unknown task or teammate for this team")

    def permitted(self, identifier, task, agent):
        bindings = self.runtime.store.rows(
            "SELECT task,agent FROM conversation WHERE thread=? UNION SELECT task,agent FROM binding WHERE thread=?",
            (identifier, identifier))
        associations = self.runtime.store.rows(
            "SELECT task,agent FROM external_association WHERE owner=? AND thread=?",
            (self.owner_id, identifier))
        return all(row["task"] == task and row["agent"] == agent for row in bindings + associations)

    def metadata(self, thread, task, agent):
        identifier = str(uuid.UUID(thread["id"]))
        if not self.permitted(identifier, task, agent):
            raise ValueError("Thread is unavailable in this scope")
        owned = self.runtime.store.rows("SELECT id FROM conversation WHERE thread=?", (identifier,))
        associated = bool(self.runtime.store.rows(
            "SELECT thread FROM external_association WHERE owner=? AND thread=?", (self.owner_id, identifier)))
        raw_status = thread.get("status") or {}
        kind = raw_status.get("type") if isinstance(raw_status, dict) else None
        status = {"type": kind if kind in {"notLoaded", "idle", "active", "systemError"} else "unknown"}
        return {"id": identifier, "ownerId": self.owner_id,
                "name": str(thread.get("name") or "Untitled thread")[:200],
                "cwd": str(thread.get("cwd") or "")[:1000], "status": status,
                "createdAt": thread.get("createdAt"), "updatedAt": thread.get("updatedAt"),
                "conversationId": owned[0]["id"] if owned else None,
                "associated": associated, "metadataOnly": True, "canAttachWriter": False,
                "canAssociate": not associated,
                "reason": "Already associated with this teammate" if associated else
                          "Metadata only; execution remains with its owner",
                "attachmentReason": "Execution handoff requires a compatible owner and tool bridge"}

    async def list(self, task, agent, team, cursor=None, limit=25):
        self.scope(task, agent, team)
        if cursor is not None and (not isinstance(cursor, str) or len(cursor) > 2048):
            raise ValueError("Invalid thread cursor")
        if not 1 <= limit <= 100:
            raise ValueError("Thread page limit must be 1 to 100")
        response = await self.runtime.rpc.call("thread/list", {
            "cursor": cursor, "limit": limit, "useStateDbOnly": True,
            "sortKey": "updated_at", "sortDirection": "desc"})
        data = [self.metadata(thread, task, agent) for thread in response["data"][:limit]
                if self.permitted(str(uuid.UUID(thread["id"])), task, agent)]
        return {"data": data, "nextCursor": response.get("nextCursor"),
                "ownerId": self.owner_id, "ownerDisplayName": socket.gethostname(),
                "scope": "runtime", "task": task, "agent": agent,
                "metadataOnly": True, "canAttachWriter": False}

    async def read(self, identifier, task, agent, team):
        self.scope(task, agent, team)
        identifier = str(uuid.UUID(identifier))
        if not self.permitted(identifier, task, agent):
            raise ValueError("Thread is unavailable in this scope")
        response = await self.runtime.rpc.call("thread/read", {"threadId": identifier, "includeTurns": False})
        if response["thread"]["id"] != identifier:
            raise ValueError("Thread metadata identity mismatch")
        return self.metadata(response["thread"], task, agent)

    async def associate(self, identifier, command_id, expected_owner, task, agent, team, action):
        self.scope(task, agent, team)
        identifier = str(uuid.UUID(identifier))
        if expected_owner != self.owner_id:
            raise Conflict("Runtime owner changed; refresh discovery before associating")
        if not isinstance(command_id, str) or not 1 <= len(command_id) <= 128:
            raise ValueError("A bounded commandId is required")
        if action not in {"associate", "dissociate"}:
            raise ValueError("Unknown association action")
        payload = json.dumps([identifier, expected_owner, task, agent, team, action])
        rows = self.runtime.store.rows("SELECT * FROM external_command WHERE id=?", (command_id,))
        if rows:
            if rows[0]["payload"] != payload:
                raise Conflict("Command ID already used with different contents")
            return json.loads(rows[0]["result"])
        if action == "associate":
            await self.read(identifier, task, agent, team)
        # Recheck after metadata RPC: concurrent requests may have claimed the
        # association. Only this local mapping and its receipt change together.
        with self.runtime.store.db:
            rows = self.runtime.store.rows("SELECT * FROM external_command WHERE id=?", (command_id,))
            if rows:
                if rows[0]["payload"] != payload:
                    raise Conflict("Command ID already used with different contents")
                return json.loads(rows[0]["result"])
            if action == "associate" and not self.permitted(identifier, task, agent):
                raise Conflict("Thread already belongs to another target")
            existing = self.runtime.store.rows(
                "SELECT task,agent FROM external_association WHERE owner=? AND thread=?",
                (self.owner_id, identifier))
            if any(row["task"] != task or row["agent"] != agent for row in existing):
                raise Conflict("Association belongs to another target")
            if action == "associate":
                self.runtime.store.db.execute("INSERT OR IGNORE INTO external_association VALUES(?,?,?,?)",
                                               (self.owner_id, identifier, task, agent))
            else:
                self.runtime.store.db.execute("DELETE FROM external_association WHERE owner=? AND thread=?",
                                               (self.owner_id, identifier))
            result = {"state": "confirmed", "action": action,
                      "association": {"ownerId": self.owner_id, "threadId": identifier,
                                      "task": task, "agent": agent, "mode": "metadata"},
                      "canAttachWriter": False}
            self.runtime.store.db.execute("INSERT INTO external_command VALUES(?,?,?)",
                                           (command_id, payload, json.dumps(result)))
        return result

    def associations(self, task, agent, team, cursor="", limit=25):
        self.scope(task, agent, team)
        if not 1 <= limit <= 100 or len(cursor) > 128:
            raise ValueError("Invalid association page")
        rows = self.runtime.store.rows(
            "SELECT thread FROM external_association WHERE owner=? AND task=? AND agent=? AND thread>? ORDER BY thread LIMIT ?",
            (self.owner_id, task, agent, cursor, limit + 1))
        return {"ownerId": self.owner_id, "ownerDisplayName": socket.gethostname(),
                "metadataOnly": True, "canAttachWriter": False,
                "data": [{"ownerId": self.owner_id, "threadId": row["thread"], "task": task,
                          "agent": agent, "mode": "metadata"} for row in rows[:limit]],
                "nextCursor": rows[limit - 1]["thread"] if len(rows) > limit else None}
