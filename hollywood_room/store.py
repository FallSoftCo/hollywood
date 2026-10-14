"""Transactional room state, replay cursors and an at-least-once delivery outbox."""

import hashlib
import json
import re
import sqlite3
import time
from pathlib import Path

from filelock import FileLock

from .models import TEAM
from .presence import ensure_schema as ensure_presence_schema
from .recovery import apply_supersessions


class Conflict(ValueError):
    pass


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = FileLock(str(path) + ".lock")
        self.lock.acquire(timeout=0)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version > 3:
            self.close()
            raise ValueError("Room database was created by a newer Hollywood release")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS team (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, coordinator TEXT NOT NULL UNIQUE,
                lobby TEXT NOT NULL UNIQUE
            );
            CREATE TABLE IF NOT EXISTS teammate (
                id TEXT PRIMARY KEY, team TEXT NOT NULL REFERENCES team(id), role TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS task (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'working',
                source INTEGER, created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS binding (
                task TEXT NOT NULL REFERENCES task(id), agent TEXT NOT NULL,
                thread TEXT UNIQUE, status TEXT NOT NULL DEFAULT 'idle', turn TEXT,
                paused INTEGER NOT NULL DEFAULT 0, cursor INTEGER NOT NULL DEFAULT 0,
                turns INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(task, agent)
            );
            CREATE TABLE IF NOT EXISTS event (
                id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, task TEXT NOT NULL,
                author TEXT NOT NULL, body TEXT NOT NULL, data TEXT NOT NULL,
                visibility TEXT NOT NULL DEFAULT 'room', created_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS event_conversation ON event(visibility,id);
            CREATE INDEX IF NOT EXISTS event_conversation_task ON event(visibility,task,id);
            CREATE TABLE IF NOT EXISTS command (
                id TEXT PRIMARY KEY, digest TEXT NOT NULL, event INTEGER REFERENCES event(id)
            );
            CREATE TABLE IF NOT EXISTS tool_receipt (
                thread TEXT NOT NULL, call TEXT NOT NULL, digest TEXT NOT NULL,
                result TEXT, PRIMARY KEY(thread,call)
            );
            CREATE TABLE IF NOT EXISTS work_order (
                event INTEGER NOT NULL REFERENCES event(id),
                owner TEXT NOT NULL REFERENCES teammate(id),
                task TEXT NOT NULL REFERENCES task(id),
                source INTEGER NOT NULL REFERENCES event(id),
                dependencies TEXT NOT NULL, acceptance TEXT NOT NULL,
                PRIMARY KEY(event,owner)
            );
            CREATE TABLE IF NOT EXISTS delivery (
                id INTEGER PRIMARY KEY AUTOINCREMENT, event INTEGER NOT NULL REFERENCES event(id),
                task TEXT NOT NULL, agent TEXT NOT NULL, authority TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'queued', turn TEXT, error TEXT,
                depth INTEGER NOT NULL DEFAULT 0, UNIQUE(event, task, agent)
            );
            CREATE TABLE IF NOT EXISTS attention (
                id INTEGER PRIMARY KEY REFERENCES event(id), task TEXT NOT NULL,
                agent TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'open', response TEXT
            );
            CREATE TABLE IF NOT EXISTS attention_dismissal (
                id INTEGER PRIMARY KEY REFERENCES attention(id), created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS push_outbox (
                event INTEGER PRIMARY KEY REFERENCES event(id), state TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0, next_at INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS device (
                id TEXT PRIMARY KEY, token TEXT NOT NULL, revoked INTEGER NOT NULL DEFAULT 0,
                after_event INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS push_delivery (
                event INTEGER NOT NULL REFERENCES event(id), device TEXT NOT NULL REFERENCES device(id),
                state TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
                next_at INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(event,device)
            );
        """)
        # Upgrade pre-release fixtures without discarding their replay history.
        delivery_columns = {row["name"] for row in self.rows("PRAGMA table_info(delivery)")}
        for name, definition in (
            ("attempts", "INTEGER NOT NULL DEFAULT 0"),
            ("next_at", "REAL NOT NULL DEFAULT 0"),
            ("phase", "TEXT"),
        ):
            if name not in delivery_columns:
                self.db.execute(f"ALTER TABLE delivery ADD COLUMN {name} {definition}")
        if "after_event" not in {row["name"] for row in self.rows("PRAGMA table_info(device)")}:
            self.db.execute("ALTER TABLE device ADD COLUMN after_event INTEGER NOT NULL DEFAULT 0")
        ensure_presence_schema(self)
        from .rooms import ensure_schema as ensure_room_schema
        ensure_room_schema(self)
        if "team" not in {row["name"] for row in self.rows("PRAGMA table_info(task)")}:
            self.db.execute("ALTER TABLE task ADD COLUMN team TEXT NOT NULL DEFAULT 'default'")
        self.db.execute("CREATE INDEX IF NOT EXISTS task_team ON task(team,id)")
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS delivery_queue ON delivery(state,task,agent,id)"
        )
        self.db.execute("""INSERT OR IGNORE INTO attention_dismissal(id,created_at)
            SELECT a.id,e.created_at FROM attention a JOIN event e ON e.id=a.id
            WHERE a.state='dismissed'""")
        self.db.execute(f"PRAGMA user_version={max(version, 2)}")
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO team VALUES('default','Default team','coordinator','lobby')"
            )
            self.db.executemany(
                "INSERT OR IGNORE INTO teammate VALUES(?,'default',?)", TEAM.items()
            )
            self.db.execute(
                "INSERT OR IGNORE INTO task(id,title,created_at) VALUES(?,?,?)",
                ("lobby", "Project room", int(time.time())),
            )
            apply_supersessions(self, Path(str(path) + ".supersessions.json"))
            # This retired check failed before thread/turn dispatch. Only its exact
            # failure is safe to retry; transport failures may already have delivered.
            self.db.execute(
                """UPDATE delivery SET state='queued',error=NULL
                   WHERE state='uncertain' AND turn IS NULL AND error=?""",
                ("Automatic turn budget reached; human review required",),
            )
            # The service cannot infer whether an in-flight tool effect completed before a crash.
            self.db.execute(
                """UPDATE delivery SET error='Runtime stopped during dispatch; delivery unconfirmed'
                   WHERE state='dispatching' AND error=?""",
                ("Automatic turn budget reached; human review required",),
            )
            # Only a persisted pre-submission phase proves restart replay is safe.
            self.db.execute("""UPDATE delivery SET state='queued'
                WHERE state='dispatching' AND phase='preparingThread'""")
            self.db.execute("UPDATE delivery SET state='uncertain' WHERE state='dispatching'")
            self.db.execute("""UPDATE delivery SET state='uncertain',next_at=0
                WHERE state='confirmed' AND EXISTS (
                    SELECT 1 FROM binding b WHERE b.task=delivery.task AND b.agent=delivery.agent
                    AND b.turn=delivery.turn AND b.status IN ('working','unreachable'))""")
            self.db.execute("UPDATE binding SET status='unreachable' WHERE status='working'")

    def rows(self, sql, params=()):
        return [dict(row) for row in self.db.execute(sql, params)]

    def event(self, kind, task, author, body, data=None, *, visibility="room"):
        row = self.db.execute(
            "INSERT INTO event(kind,task,author,body,data,visibility,created_at) VALUES(?,?,?,?,?,?,?)",
            (kind, task, author, body, json.dumps(data or {}), visibility, int(time.time())),
        )
        return row.lastrowid

    def get_event(self, event_id):
        row = self.rows("SELECT * FROM event WHERE id=?", (event_id,))[0]
        row["data"] = json.loads(row["data"])
        return row

    def events(self, *, after=0, task=None, limit=100, visibility=None, team=None):
        sql, args = "SELECT id FROM event WHERE id>?", [after]
        if team:
            sql += " AND task IN (SELECT id FROM task WHERE team=?)"
            args.append(team)
        if task:
            sql += " AND task=?"
            args.append(task)
        if visibility:
            sql += " AND visibility IN ('room',?)"
            args.append(visibility)
        sql += " ORDER BY id LIMIT ?"
        args.append(min(max(limit, 1), 100))
        return [self.get_event(row["id"]) for row in self.rows(sql, args)]

    def ensure_binding(self, task, agent):
        if agent not in self.roster(self.task_team(task)):
            raise ValueError("Unknown teammate for task team")
        self.db.execute("INSERT OR IGNORE INTO binding(task,agent) VALUES(?,?)", (task, agent))

    def queue(self, event, task, agent, authority, *, depth=0):
        self.ensure_binding(task, agent)
        self.db.execute(
            "INSERT OR IGNORE INTO delivery(event,task,agent,authority,depth) VALUES(?,?,?,?,?)",
            (event, task, agent, authority, depth),
        )

    def message(
        self,
        command_id,
        *,
        task,
        author,
        body,
        targets,
        authority,
        visibility="room",
        reply_to=None,
        depth=0,
        resolves=None,
        assignment=None,
    ):
        payload = {
            "task": task,
            "author": author,
            "body": body,
            "targets": targets,
            "authority": authority,
            "visibility": visibility,
            "replyTo": reply_to,
        }
        if assignment is not None:
            payload["assignment"] = assignment
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        with self.db:
            existing = self.rows("SELECT * FROM command WHERE id=?", (command_id,))
            if existing:
                if existing[0]["digest"] != digest:
                    raise Conflict("Command ID already used with different contents")
                return self.get_event(existing[0]["event"])
            if not self.rows("SELECT id FROM task WHERE id=?", (task,)):
                if assignment is None:
                    raise ValueError("Unknown task")
                self.db.execute(
                    "INSERT INTO task(id,title,source,created_at,team) VALUES(?,?,?,?,?)",
                    (
                        task,
                        assignment["title"],
                        assignment["source"],
                        int(time.time()),
                        assignment["team"]
                        if "team" in assignment
                        else self.task_team(self.get_event(assignment["source"])["task"]),
                    ),
                )
                self.event(
                    "task", task, "hollywood", assignment["title"], {"source": assignment["source"]}
                )
            event_id = self.event(
                "message",
                task,
                author,
                body,
                {"targets": targets, "replyTo": reply_to},
                visibility=visibility,
            )
            self.db.execute("INSERT INTO command VALUES(?,?,?)", (command_id, digest, event_id))
            if assignment is not None and assignment["source"] is not None:
                source = self.get_event(assignment["source"])
                if source["author"] != "you" or source["visibility"] != "room" or self.task_team(source["task"]) != self.task_team(task):
                    raise ValueError("Work order requires a public human task request in the same team")
                dependencies = assignment.get("dependencies", [])
                acceptance = assignment.get("acceptance", "")
                if not isinstance(dependencies, list) or len(dependencies) > 16 or any(
                    not isinstance(item, int) or isinstance(item, bool) or not self.rows(
                        "SELECT w.event FROM work_order w JOIN task t ON t.id=w.task WHERE w.event=? AND t.team=?",
                        (item, self.task_team(task))) for item in dependencies
                ):
                    raise ValueError("Dependencies must identify at most 16 same-team work orders")
                if not isinstance(acceptance, str) or len(acceptance) > 2000:
                    raise ValueError("Acceptance must fit 2000 characters")
                for owner in targets:
                    self.db.execute("INSERT INTO work_order VALUES(?,?,?,?,?,?)",
                        (event_id, owner, task, source["id"], json.dumps(dependencies), acceptance))
            if assignment is not None and assignment["source"] is None and authority == "user":
                self.db.execute("UPDATE task SET source=? WHERE id=?", (event_id, task))
            for agent in targets:
                if assignment is not None and assignment.get("dependencies"):
                    # Persist the order, but no execution until dependency acceptance
                    # and the native goal admission contract are implemented.
                    continue
                if (
                    assignment is not None
                    and assignment["source"] is not None
                    and not self.rows(
                        "SELECT agent FROM binding WHERE task=? AND agent=?", (task, agent)
                    )
                ):
                    source = self.get_event(assignment["source"])
                    if source["author"] != "you" or source["visibility"] != "room":
                        raise ValueError("Delegation requires a public human task request")
                    self.queue(source["id"], task, agent, "userReference")
                self.queue(event_id, task, agent, authority, depth=depth)
            if resolves is not None:
                self.db.execute(
                    "UPDATE attention SET state='resolved',response=? WHERE id=?", (body, resolves)
                )
                self.event(
                    "resolved",
                    task,
                    "you",
                    "Question answered",
                    {"attention": resolves},
                    visibility=visibility,
                )
            return self.get_event(event_id)

    def add_task(self, task, title, source):
        with self.db:
            self.db.execute(
                "INSERT INTO task(id,title,source,created_at) VALUES(?,?,?,?)",
                (task, title, source, int(time.time())),
            )
            return self.event("task", task, "hollywood", title, {"source": source})

    def snapshot(self, team=None):
        scope, args = (" WHERE team=?", (team,)) if team else ("", ())
        task_ids = "SELECT id FROM task" + scope
        return {
            "tasks": self.rows("SELECT * FROM task" + scope + " ORDER BY created_at", args),
            "agents": self.rows(
                f"SELECT * FROM binding WHERE task IN ({task_ids}) ORDER BY task,agent", args
            ),
            "attention": self.rows(
                f"SELECT * FROM attention WHERE state='open' AND task IN ({task_ids}) ORDER BY id",
                args,
            ),
            "deliveries": self.rows(
                f"SELECT * FROM delivery WHERE state!='confirmed' AND task IN ({task_ids}) ORDER BY id LIMIT 100",
                args,
            ),
            "cursor": self.db.execute("SELECT coalesce(max(id),0) FROM event").fetchone()[0],
        }

    def roster(self, team=None):
        rows = self.rows(
            "SELECT * FROM teammate" + (" WHERE team=?" if team else ""), (team,) if team else ()
        )
        return {row["id"]: row["role"] for row in rows}

    def roster_page(self, team, *, cursor="", limit=100):
        rows = self.rows(
            "SELECT id,role FROM teammate WHERE team=? AND id>? ORDER BY id LIMIT ?",
            (team, cursor, limit + 1),
        )
        return {
            "team": {row["id"]: row["role"] for row in rows[:limit]},
            "total": self.db.execute(
                "SELECT count(*) FROM teammate WHERE team=?", (team,)
            ).fetchone()[0],
            "next_cursor": rows[limit - 1]["id"] if len(rows) > limit else None,
        }

    def team_page(self, *, cursor="", limit=100):
        rows = self.rows("SELECT * FROM team WHERE id>? ORDER BY id LIMIT ?", (cursor, limit + 1))
        teams = []
        for row in rows[:limit]:
            roster = self.roster_page(row["id"])
            teams.append(
                {
                    **row,
                    "members": list(roster["team"]),
                    "members_total": roster["total"],
                    "members_next_cursor": roster["next_cursor"],
                }
            )
        return {
            "teams": teams,
            "total": self.db.execute("SELECT count(*) FROM team").fetchone()[0],
            "next_cursor": rows[limit - 1]["id"] if len(rows) > limit else None,
        }

    def teams(self):
        return [
            {**row, "members": list(self.roster(row["id"]))}
            for row in self.rows("SELECT * FROM team ORDER BY id")
        ]

    def task_team(self, task):
        rows = self.rows("SELECT team FROM task WHERE id=?", (task,))
        if not rows:
            raise ValueError("Unknown task")
        return rows[0]["team"]

    def coordinator(self, task):
        return self.rows("SELECT coordinator FROM team WHERE id=?", (self.task_team(task),))[0][
            "coordinator"
        ]

    @staticmethod
    def validate_identity(value):
        if not re.fullmatch(r"[a-z0-9_-]{1,64}", value) or value in {
            "room",
            "you",
            "hollywood",
            "system",
        }:
            raise ValueError("Invalid or reserved identity")

    def add_teammate(self, agent, team, role):
        self.validate_identity(agent)
        if not role.strip() or len(role) > 2000:
            raise ValueError("Role must contain text and fit within 2000 characters")
        if not self.rows("SELECT id FROM team WHERE id=?", (team,)):
            raise ValueError("Unknown team")
        existing = self.rows("SELECT * FROM teammate WHERE id=?", (agent,))
        if existing:
            if existing[0] == {"id": agent, "team": team, "role": role}:
                return existing[0]
            raise Conflict("Teammate ID already exists")
        with self.db:
            self.db.execute("INSERT INTO teammate VALUES(?,?,?)", (agent, team, role))
        return {"id": agent, "team": team, "role": role}

    def add_team(self, team, name, coordinator, role):
        self.validate_identity(team)
        self.validate_identity(coordinator)
        if (
            len(team) > 59
            or not name.strip()
            or len(name) > 100
            or not role.strip()
            or len(role) > 2000
        ):
            raise ValueError("Invalid team name, ID or coordinator role")
        lobby = "team-" + team
        existing = self.rows("SELECT * FROM team WHERE id=?", (team,))
        if existing:
            if (
                existing[0]
                == {"id": team, "name": name, "coordinator": coordinator, "lobby": lobby}
                and self.roster(team).get(coordinator) == role
            ):
                return next(item for item in self.teams() if item["id"] == team)
            raise Conflict("Team ID already exists")
        if self.rows("SELECT id FROM teammate WHERE id=?", (coordinator,)) or self.rows(
            "SELECT id FROM task WHERE id=?", (lobby,)
        ):
            raise Conflict("Coordinator or lobby ID already exists")
        with self.db:
            self.db.execute("INSERT INTO team VALUES(?,?,?,?)", (team, name, coordinator, lobby))
            self.db.execute("INSERT INTO teammate VALUES(?,?,?)", (coordinator, team, role))
            self.db.execute(
                "INSERT INTO task(id,title,created_at,team) VALUES(?,?,?,?)",
                (lobby, name, int(time.time()), team),
            )
        return next(item for item in self.teams() if item["id"] == team)

    def attention_card_state(self, event):
        rows = self.rows(
            """SELECT a.id,a.state,d.created_at FROM attention a
            JOIN event e ON e.id=a.id LEFT JOIN attention_dismissal d ON d.id=a.id
            WHERE a.id IN (?,?) AND e.task=? AND e.visibility IN ('room',?)
            ORDER BY CASE WHEN a.id=? THEN 0 ELSE 1 END LIMIT 1""",
            (
                event["id"],
                event["data"].get("replyTo") or event["data"].get("attention"),
                event["task"],
                event["visibility"],
                event["id"],
            ),
        )
        if not rows:
            return {}
        row = rows[0]
        return {
            "attentionId": row["id"],
            "attentionState": row["state"],
            "dismissed": row["created_at"] is not None,
            "dismissedAt": row["created_at"],
        }

    def dismiss_attention(self, command_id, event_id, team):
        digest = hashlib.sha256(
            json.dumps(["attention/dismiss", team, event_id]).encode()
        ).hexdigest()
        with self.db:
            source = self.get_event(event_id)
            if self.task_team(source["task"]) != team:
                raise ValueError("Attention item does not belong to this team")
            rows = self.rows("SELECT * FROM attention WHERE id=?", (event_id,))
            if not rows:
                raise Conflict("Unknown attention item")
            prior = self.rows("SELECT * FROM command WHERE id=?", (command_id,))
            if prior and prior[0]["digest"] != digest:
                raise Conflict("Command ID already used with different contents")
            state = rows[0]["state"]
            if source["kind"] == "approval" and state == "open":
                raise Conflict("Review or decline this live approval explicitly")
            receipt = event_id
            if state == "open":
                state = "dismissed"
                self.db.execute("UPDATE attention SET state='dismissed' WHERE id=?", (event_id,))
            if not self.rows("SELECT id FROM attention_dismissal WHERE id=?", (event_id,)):
                self.db.execute(
                    "INSERT INTO attention_dismissal VALUES(?,?)", (event_id, int(time.time()))
                )
                receipt = self.event(
                    "dismissed",
                    source["task"],
                    "you",
                    "Attention card dismissed",
                    {"attention": event_id, "state": state, "dismissed": True},
                    visibility=source["visibility"],
                )
            if not prior:
                self.db.execute("INSERT INTO command VALUES(?,?,?)", (command_id, digest, receipt))
            return {"id": event_id, "state": state, "dismissed": True}

    def attention(self, task, agent, text, data=None, *, visibility="room", command_id=None):
        digest = hashlib.sha256(json.dumps(
            ["attention", task, agent, text, data, visibility], sort_keys=True
        ).encode()).hexdigest()
        with self.db:
            if command_id is not None:
                previous = self.rows("SELECT * FROM command WHERE id=?", (command_id,))
                if previous:
                    if previous[0]["digest"] != digest:
                        raise Conflict("Question command ID already used with different contents")
                    return previous[0]["event"]
            event = self.event("attention", task, agent, text, data, visibility=visibility)
            self.db.execute(
                "INSERT INTO attention(id,task,agent) VALUES(?,?,?)", (event, task, agent)
            )
            self.db.execute("INSERT INTO push_outbox(event) VALUES(?)", (event,))
            if command_id is not None:
                self.db.execute("INSERT INTO command VALUES(?,?,?)", (command_id, digest, event))
            return event

    def close(self):
        self.db.close()
        self.lock.release()
