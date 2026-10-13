"""Preserve inactive conversations while binding remains the active execution projection."""

import json
import time
import uuid

from .store import Conflict


class Conversations:
    def __init__(self, runtime):
        self.runtime, self.store = runtime, runtime.store
        db = self.store.db
        db.executescript("""
            CREATE TABLE IF NOT EXISTS conversation (
                id TEXT PRIMARY KEY, task TEXT NOT NULL, agent TEXT NOT NULL,
                label TEXT NOT NULL, created_at INTEGER NOT NULL,
                thread TEXT UNIQUE, status TEXT NOT NULL, turn TEXT,
                paused INTEGER NOT NULL, cursor INTEGER NOT NULL, turns INTEGER NOT NULL,
                attached INTEGER NOT NULL DEFAULT 1,
                FOREIGN KEY(task,agent) REFERENCES binding(task,agent)
            );
            CREATE TABLE IF NOT EXISTS conversation_command (
                id TEXT PRIMARY KEY, payload TEXT NOT NULL, result TEXT NOT NULL
            );
        """)
        if "conversation_id" not in {r["name"] for r in self.store.rows("PRAGMA table_info(binding)")}:
            db.execute("ALTER TABLE binding ADD COLUMN conversation_id TEXT")
        with db:
            for binding in self.store.rows("SELECT * FROM binding WHERE conversation_id IS NULL"):
                self.capture(binding)
            db.execute("PRAGMA user_version=3")

    def capture(self, binding):
        identifier = "c-" + uuid.uuid5(uuid.NAMESPACE_URL,
                                      json.dumps([binding["task"], binding["agent"]])).hex
        self.store.db.execute(
            "INSERT OR IGNORE INTO conversation VALUES(?,?,?,?,?,?,?,?,?,?,?,1)",
            (identifier, binding["task"], binding["agent"], "Original conversation", int(time.time()),
             *[binding[k] for k in ("thread", "status", "turn", "paused", "cursor", "turns")]))
        self.store.db.execute("UPDATE binding SET conversation_id=? WHERE task=? AND agent=?",
                              (identifier, binding["task"], binding["agent"]))
        return identifier

    def sync(self):
        with self.store.db:
            for binding in self.store.rows("SELECT * FROM binding"):
                identifier = binding["conversation_id"] or self.capture(binding)
                self.store.db.execute(
                    "UPDATE conversation SET thread=?,status=?,turn=?,paused=?,cursor=?,turns=? WHERE id=?",
                    (*[binding[k] for k in ("thread", "status", "turn", "paused", "cursor", "turns")], identifier))

    def get(self, identifier, team):
        rows = self.store.rows("SELECT c.* FROM conversation c JOIN task t ON t.id=c.task WHERE c.id=? AND t.team=?",
                               (identifier, team))
        if not rows:
            raise ValueError("Unknown conversation for this team")
        return rows[0]

    def list(self, task, agent, team, cursor="", limit=50):
        if self.store.task_team(task) != team or agent not in self.store.roster(team):
            raise ValueError("Unknown task or agent for this team")
        self.sync()
        rows = self.store.rows("SELECT * FROM conversation WHERE task=? AND agent=? AND id>? ORDER BY id LIMIT ?",
                               (task, agent, cursor, min(max(limit, 1), 100) + 1))
        count = min(max(limit, 1), 100)
        selected = self.store.rows("SELECT conversation_id FROM binding WHERE task=? AND agent=?", (task, agent))
        return {"data": rows[:count],
                "currentConversationId": selected[0]["conversation_id"] if selected else None,
                "nextCursor": rows[count - 1]["id"] if len(rows) > count else None}

    def idle(self, task, agent):
        key = (task, agent)
        if key in self.runtime.active or key in self.runtime.dispatchers or key in self.runtime.retiring:
            raise Conflict("Finish or stop the active turn before changing conversations")
        if self.store.rows("SELECT id FROM delivery WHERE task=? AND agent=? AND state IN ('queued','dispatching','uncertain')",
                           key):
            raise Conflict("Resolve pending deliveries before changing conversations")
        rows = self.store.rows("SELECT turn,status FROM binding WHERE task=? AND agent=?", key)
        if rows and (rows[0]["turn"] or rows[0]["status"] == "unreachable"):
            raise Conflict("Reconcile the existing turn before changing conversations")

    async def command(self, command_id, action, team, *, identifier=None, task=None, agent=None, label="New conversation"):
        if not isinstance(command_id, str) or not 1 <= len(command_id) <= 128:
            raise ValueError("A bounded commandId is required")
        payload = json.dumps([action, team, identifier, task, agent, label])
        previous = self.store.rows("SELECT * FROM conversation_command WHERE id=?", (command_id,))
        if previous:
            if previous[0]["payload"] != payload:
                raise Conflict("Command ID already used with different contents")
            return json.loads(previous[0]["result"])
        self.sync()
        current_resume = False
        if action == "resume":
            row = self.get(identifier, team)
            selected = self.store.rows("SELECT conversation_id FROM binding WHERE task=? AND agent=?", (row["task"], row["agent"]))[0]
            current_resume = selected["conversation_id"] == identifier
        if action == "stop" or current_resume:
            row = self.get(identifier, team)
            task, agent = row["task"], row["agent"]
            selected = self.store.rows("SELECT conversation_id FROM binding WHERE task=? AND agent=?", (task, agent))[0]
            if selected["conversation_id"] != identifier:
                raise Conflict("Cannot stop an inactive conversation")
            # Bind the receipt before awaiting the runtime; a retry must never stop
            # a different conversation after selection changes.
            result = {"conversationId": identifier, "action": action, "state": "pending"}
            with self.store.db:
                self.store.db.execute("INSERT INTO conversation_command VALUES(?,?,?)", (command_id, payload, json.dumps(result)))
            outcome = await self.runtime.control(task, agent, "resume" if current_resume else "interrupt",
                                                 command_id="conversation:" + command_id)
            result.update(outcome)
            result["action"] = action
            with self.store.db:
                self.store.db.execute("UPDATE conversation_command SET result=? WHERE id=?", (json.dumps(result), command_id))
            return result
        # No awaits within this transaction: selection, archive and receipt commit
        # together, and the runtime cannot dispatch between the idle check and swap.
        with self.store.db:
            if action == "new":
                if self.store.task_team(task) != team or agent not in self.store.roster(team):
                    raise ValueError("Unknown task or agent for this team")
                if not isinstance(label, str) or not 1 <= len(label) <= 100:
                    raise ValueError("Label must contain 1 to 100 characters")
                self.idle(task, agent)
                self.store.ensure_binding(task, agent)
                identifier = "c-" + uuid.uuid4().hex
                self.store.db.execute("INSERT INTO conversation VALUES(?,?,?,?,?,NULL,'idle',NULL,0,0,0,1)",
                                      (identifier, task, agent, label, int(time.time())))
            else:
                row = self.get(identifier, team)
                task, agent = row["task"], row["agent"]
            if action in {"new", "resume"}:
                self.idle(task, agent)
                row = self.get(identifier, team)
                self.store.db.execute(
                    "UPDATE binding SET conversation_id=?,thread=?,status='idle',turn=NULL,paused=0,cursor=?,turns=? WHERE task=? AND agent=?",
                    (identifier, row["thread"], row["cursor"], row["turns"], task, agent))
            elif action in {"attach", "detach"}:
                self.store.db.execute("UPDATE conversation SET attached=? WHERE id=?", (action == "attach", identifier))
            else:
                raise ValueError("Unknown conversation action")
            result = {"conversationId": identifier, "action": action, "state": "confirmed"}
            self.store.db.execute("INSERT INTO conversation_command VALUES(?,?,?)", (command_id, payload, json.dumps(result)))
        return result
