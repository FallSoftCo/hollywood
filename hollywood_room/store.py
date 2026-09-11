"""Transactional room state, replay cursors and an at-least-once delivery outbox."""

import hashlib
import json
import sqlite3
import time
from pathlib import Path

from filelock import FileLock


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
        if version > 1:
            self.close()
            raise ValueError("Room database was created by a newer Hollywood release")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
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
            CREATE TABLE IF NOT EXISTS command (
                id TEXT PRIMARY KEY, digest TEXT NOT NULL, event INTEGER REFERENCES event(id)
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
        if "after_event" not in {row["name"] for row in self.rows("PRAGMA table_info(device)")}:
            self.db.execute("ALTER TABLE device ADD COLUMN after_event INTEGER NOT NULL DEFAULT 0")
        self.db.execute("PRAGMA user_version=1")
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO task(id,title,created_at) VALUES(?,?,?)",
                ("lobby", "Project room", int(time.time())),
            )
            # The service cannot infer whether an in-flight tool effect completed before a crash.
            self.db.execute("UPDATE delivery SET state='uncertain' WHERE state='dispatching'")
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

    def events(self, *, after=0, task=None, limit=100, visibility=None):
        sql, args = "SELECT id FROM event WHERE id>?", [after]
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
                if self.db.execute("SELECT count(*) FROM task").fetchone()[0] >= 100:
                    raise ValueError("Project task limit reached")
                self.db.execute(
                    "INSERT INTO task(id,title,source,created_at) VALUES(?,?,?,?)",
                    (task, assignment["title"], assignment["source"], int(time.time())),
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
            for agent in targets:
                if assignment is not None and not self.rows(
                    "SELECT agent FROM binding WHERE task=? AND agent=?", (task, agent)
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

    def snapshot(self):
        return {
            "tasks": self.rows("SELECT * FROM task ORDER BY created_at"),
            "agents": self.rows("SELECT * FROM binding ORDER BY task,agent"),
            "attention": self.rows("SELECT * FROM attention WHERE state='open' ORDER BY id"),
            "deliveries": self.rows(
                "SELECT * FROM delivery WHERE state!='confirmed' ORDER BY id LIMIT 100"
            ),
            "cursor": self.db.execute("SELECT coalesce(max(id),0) FROM event").fetchone()[0],
        }

    def attention(self, task, agent, text, data=None, *, visibility="room"):
        with self.db:
            event = self.event("attention", task, agent, text, data, visibility=visibility)
            self.db.execute(
                "INSERT INTO attention(id,task,agent) VALUES(?,?,?)", (event, task, agent)
            )
            self.db.execute("INSERT INTO push_outbox(event) VALUES(?)", (event,))
            return event

    def close(self):
        self.db.close()
        self.lock.release()
