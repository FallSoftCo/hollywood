#!/usr/bin/env python3
"""Hollywood: local pull-based chatroom service for CLI agents."""

from __future__ import annotations

import argparse
import base64
import json
import os
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

API_PREFIX = "/hollywood/v1"
DEFAULT_DB = os.environ.get("HOLLYWOOD_DB", str(Path.home() / ".hollywood" / "hollywood.db"))
DEFAULT_URL = os.environ.get("HOLLYWOOD_URL", "http://127.0.0.1:8765")
DEFAULT_ROOM = os.environ.get("HOLLYWOOD_ROOM", "main")
DEFAULT_CURSOR_DIR = os.environ.get("HOLLYWOOD_CURSOR_DIR", str(Path.home() / ".hollywood" / "cursors"))
SERVICE_VERSION = "hollywood/0.1"
SCHEMA_VERSION = 3
ROOM_CONTRACT_VERSION = "losangelex-room/v2"
INITIAL_ROOM_STATE_VERSION = 1
REGISTRY_STALE_AFTER = timedelta(
    seconds=int(os.environ.get("HOLLYWOOD_REGISTRY_STALE_AFTER_SECONDS", "90"))
)
ALIAS_PREFIX = "sid-"
MESSAGE_KIND_AMBIENT = "ambient"
MESSAGE_KIND_BROADCAST = "broadcast"
MESSAGE_KIND_DIRECT = "direct"
VALID_MESSAGE_KINDS = {
    MESSAGE_KIND_AMBIENT,
    MESSAGE_KIND_BROADCAST,
    MESSAGE_KIND_DIRECT,
}
RESPONSE_POLICY_REQUIRED = "required"
RESPONSE_POLICY_OPTIONAL = "optional"
RESPONSE_POLICY_NONE = "none"
VALID_RESPONSE_POLICIES = {
    RESPONSE_POLICY_REQUIRED,
    RESPONSE_POLICY_OPTIONAL,
    RESPONSE_POLICY_NONE,
}
VALID_REGISTRY_STATUSES = {
    "unknown",
    "idle",
    "active",
    "waiting",
    "blocked",
    "done",
}
VALID_TEAM_MEMBER_STATES = {
    "pending",
    "accepted",
    "joined",
    "active",
    "declined",
    "deferred",
    "timed_out",
}
ROOM_KIND_MAIN = "main"
ROOM_KIND_REPO = "repo"
ROOM_KIND_TASK = "task"
ROOM_KIND_MULTI = "multi"
ROOM_KIND_ORG = "org"
KNOWN_TYPED_ROOM_KINDS = {
    ROOM_KIND_REPO,
    ROOM_KIND_TASK,
    ROOM_KIND_MULTI,
    ROOM_KIND_ORG,
}
VALID_COORDINATION_POLICIES = {
    "leader_award",
    "kanban_pull",
    "dual_command_lease",
    "auto",
}
VALID_COORDINATION_PHASES = {
    "discovery",
    "execution",
    "stabilization",
    "closure",
}


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_registry_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def registry_row_is_fresh(
    row: sqlite3.Row,
    now: datetime | None = None,
    stale_after: timedelta = REGISTRY_STALE_AFTER,
) -> bool:
    if now is None:
        now = datetime.now(timezone.utc)
    heartbeat_at = parse_registry_timestamp(row["last_heartbeat_at"]) or parse_registry_timestamp(
        row["updated_at"]
    )
    return heartbeat_at is not None and heartbeat_at >= now - stale_after


def slugify_room_segment(value: str, *, fallback: str = "workspace") -> str:
    pieces: list[str] = []
    previous_was_sep = False
    for ch in value.strip().lower():
        if ch.isalnum():
            pieces.append(ch)
            previous_was_sep = False
        elif not previous_was_sep:
            pieces.append("-")
            previous_was_sep = True
    slug = "".join(pieces).strip("-")
    return slug or fallback


def repo_room_name(repo_slug: str) -> str:
    return f"{ROOM_KIND_REPO}/{slugify_room_segment(repo_slug, fallback='repo')}"


def task_room_name(repo_slug: str, task_slug: str) -> str:
    return (
        f"{ROOM_KIND_TASK}/{slugify_room_segment(repo_slug, fallback='repo')}/"
        f"{slugify_room_segment(task_slug, fallback='task')}"
    )


def multi_room_name(name: str) -> str:
    return f"{ROOM_KIND_MULTI}/{slugify_room_segment(name, fallback='shared')}"


def org_room_name(name: str) -> str:
    return f"{ROOM_KIND_ORG}/{slugify_room_segment(name, fallback='shared')}"


def describe_room(room: str) -> dict[str, str | None]:
    normalized = room.strip()
    if normalized == ROOM_KIND_MAIN:
        return {
            "kind": ROOM_KIND_MAIN,
            "room": ROOM_KIND_MAIN,
            "repo": None,
            "task": None,
        }
    parts = [part for part in normalized.split("/") if part]
    if len(parts) == 2 and parts[0] in {ROOM_KIND_REPO, ROOM_KIND_MULTI, ROOM_KIND_ORG}:
        return {
            "kind": parts[0],
            "room": normalized,
            "repo": parts[1] if parts[0] == ROOM_KIND_REPO else None,
            "task": None,
        }
    if len(parts) == 3 and parts[0] == ROOM_KIND_TASK:
        return {
            "kind": ROOM_KIND_TASK,
            "room": normalized,
            "repo": parts[1],
            "task": parts[2],
        }
    return {
        "kind": "custom",
        "room": normalized,
        "repo": None,
        "task": None,
    }


def session_id_to_alias(session_id: str) -> str:
    sid = uuid.UUID(session_id)
    raw = base64.b32encode(sid.bytes).decode("ascii").rstrip("=").lower()
    chunks = [raw[i : i + 4] for i in range(0, len(raw), 4)]
    return f"{ALIAS_PREFIX}{'-'.join(chunks)}"


def alias_to_session_id(alias: str) -> str:
    value = alias.strip().lower()
    if not value.startswith(ALIAS_PREFIX):
        raise ValueError(f"alias must start with {ALIAS_PREFIX}")
    token = value[len(ALIAS_PREFIX) :].replace("-", "")
    if not token:
        raise ValueError("alias payload is empty")
    pad = "=" * ((8 - (len(token) % 8)) % 8)
    raw = base64.b32decode((token + pad).upper(), casefold=True)
    if len(raw) != 16:
        raise ValueError("alias decoded to invalid byte length")
    return str(uuid.UUID(bytes=raw))


def normalize_id(value: str | None) -> str | None:
    if value is None:
        return None
    val = value.strip()
    if not val:
        return None
    if val.lower().startswith(ALIAS_PREFIX):
        return alias_to_session_id(val)
    return val


def display_identity(value: str | None) -> str:
    if not value:
        return "*"
    try:
        alias = session_id_to_alias(value)
        return f"{alias} ({value})"
    except Exception:
        return value


def ensure_parent(path: str) -> None:
    Path(path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)


def apply_base_schema_migration(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room TEXT NOT NULL,
            sender_id TEXT NOT NULL,
            recipient_id TEXT,
            message_kind TEXT NOT NULL DEFAULT 'ambient',
            response_policy TEXT NOT NULL DEFAULT 'optional',
            body TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    columns = {
        row[1] for row in conn.execute("PRAGMA table_info(messages)").fetchall()
    }
    if "message_kind" not in columns:
        conn.execute(
            "ALTER TABLE messages ADD COLUMN message_kind TEXT NOT NULL DEFAULT 'ambient'"
        )
    if "response_policy" not in columns:
        conn.execute(
            "ALTER TABLE messages ADD COLUMN response_policy TEXT NOT NULL DEFAULT 'optional'"
        )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_room_id ON messages(room, id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_recipient ON messages(recipient_id)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS registry (
            session_id TEXT PRIMARY KEY,
            room TEXT NOT NULL,
            attached INTEGER NOT NULL DEFAULT 1,
            cwd TEXT,
            repo_name TEXT,
            attention_mode TEXT,
            identities_json TEXT NOT NULL DEFAULT '[]',
            session_kind TEXT,
            resumed_from TEXT,
            ephemeral INTEGER,
            rollout_path TEXT,
            status TEXT NOT NULL DEFAULT 'unknown',
            role TEXT,
            task TEXT,
            scope TEXT,
            updated_at TEXT NOT NULL,
            last_heartbeat_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_registry_room ON registry(room)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS teams (
            team_id TEXT PRIMARY KEY,
            room TEXT NOT NULL,
            task_room TEXT,
            purpose TEXT NOT NULL,
            leader_session_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_teams_room ON teams(room, updated_at DESC)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS team_members (
            team_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'member',
            state TEXT NOT NULL DEFAULT 'pending',
            joined_room TEXT,
            task TEXT,
            scope TEXT,
            invited_by TEXT,
            invited_at TEXT NOT NULL,
            responded_at TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(team_id, session_id),
            FOREIGN KEY(team_id) REFERENCES teams(team_id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_team_members_team ON team_members(team_id, updated_at DESC)"
    )


def room_row_to_dict(row: sqlite3.Row | None) -> dict[str, Any]:
    if row is None:
        raise ValueError("room row missing")
    return dict(row)


def ensure_room_entry(
    conn: sqlite3.Connection,
    room: str,
    *,
    last_message_id: int | None = None,
) -> dict[str, Any]:
    normalized = room.strip()
    if not normalized:
        raise ValueError("room is required")
    described = describe_room(normalized)
    now = utcnow_iso()
    conn.execute(
        """
        INSERT INTO rooms(
            room, kind, repo, task, state_version, contract_version,
            created_at, updated_at, last_message_id, archived_at,
            coordination_policy, coordination_phase, coordination_epoch,
            leader_session_id, verifier_session_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, 1, NULL, NULL)
        ON CONFLICT(room) DO UPDATE SET
            kind = excluded.kind,
            repo = excluded.repo,
            task = excluded.task,
            contract_version = rooms.contract_version,
            updated_at = excluded.updated_at,
            archived_at = NULL,
            last_message_id = CASE
                WHEN excluded.last_message_id > rooms.last_message_id THEN excluded.last_message_id
                ELSE rooms.last_message_id
            END
        """,
        (
            normalized,
            described["kind"],
            described["repo"],
            described["task"],
            INITIAL_ROOM_STATE_VERSION,
            ROOM_CONTRACT_VERSION,
            now,
            now,
            max(0, int(last_message_id or 0)),
        ),
    )
    row = conn.execute("SELECT * FROM rooms WHERE room = ?", (normalized,)).fetchone()
    return room_row_to_dict(row)


def backfill_room_entries(conn: sqlite3.Connection) -> None:
    rooms: set[str] = set()
    for query in (
        "SELECT DISTINCT room FROM messages WHERE TRIM(room) != ''",
        "SELECT DISTINCT room FROM registry WHERE TRIM(room) != ''",
        "SELECT DISTINCT room FROM teams WHERE TRIM(room) != ''",
        "SELECT DISTINCT task_room FROM teams WHERE task_room IS NOT NULL AND TRIM(task_room) != ''",
        "SELECT DISTINCT joined_room FROM team_members WHERE joined_room IS NOT NULL AND TRIM(joined_room) != ''",
    ):
        rooms.update(str(row[0]).strip() for row in conn.execute(query).fetchall() if row[0])
    for room in sorted(rooms):
        last_message_row = conn.execute(
            "SELECT COALESCE(MAX(id), 0) FROM messages WHERE room = ?",
            (room,),
        ).fetchone()
        last_message_id = int(last_message_row[0]) if last_message_row is not None else 0
        ensure_room_entry(conn, room, last_message_id=last_message_id)


def apply_rooms_schema_migration(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS rooms (
            room TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            repo TEXT,
            task TEXT,
            state_version INTEGER NOT NULL DEFAULT 1,
            contract_version TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_message_id INTEGER NOT NULL DEFAULT 0,
            archived_at TEXT,
            coordination_policy TEXT,
            coordination_phase TEXT,
            coordination_epoch INTEGER NOT NULL DEFAULT 1,
            leader_session_id TEXT,
            verifier_session_id TEXT
        )
        """
    )
    columns = {
        row[1] for row in conn.execute("PRAGMA table_info(rooms)").fetchall()
    }
    if "coordination_policy" not in columns:
        conn.execute("ALTER TABLE rooms ADD COLUMN coordination_policy TEXT")
    if "coordination_phase" not in columns:
        conn.execute("ALTER TABLE rooms ADD COLUMN coordination_phase TEXT")
    if "coordination_epoch" not in columns:
        conn.execute("ALTER TABLE rooms ADD COLUMN coordination_epoch INTEGER NOT NULL DEFAULT 1")
    if "leader_session_id" not in columns:
        conn.execute("ALTER TABLE rooms ADD COLUMN leader_session_id TEXT")
    if "verifier_session_id" not in columns:
        conn.execute("ALTER TABLE rooms ADD COLUMN verifier_session_id TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rooms_kind ON rooms(kind, updated_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rooms_repo ON rooms(repo, updated_at DESC)")
    backfill_room_entries(conn)


def init_db(db_path: str) -> None:
    ensure_parent(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        current_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        if current_version > SCHEMA_VERSION:
            raise RuntimeError(
                f"Hollywood DB schema version {current_version} is newer than supported version {SCHEMA_VERSION}"
            )
        apply_base_schema_migration(conn)
        if current_version < 2:
            apply_rooms_schema_migration(conn)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
    finally:
        conn.close()


def insert_message(
    db_path: str,
    room: str,
    sender_id: str,
    recipient_id: str | None,
    message_kind: str,
    response_policy: str,
    body: str,
) -> dict[str, Any]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        created_at = utcnow_iso()
        cur = conn.execute(
            "INSERT INTO messages(room, sender_id, recipient_id, message_kind, response_policy, body, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                room,
                sender_id,
                recipient_id,
                message_kind,
                response_policy,
                body,
                created_at,
            ),
        )
        conn.commit()
        msg_id = cur.lastrowid
        ensure_room_entry(conn, room, last_message_id=int(msg_id))
        conn.commit()
        row = conn.execute("SELECT * FROM messages WHERE id = ?", (msg_id,)).fetchone()
        return dict(row)
    finally:
        conn.close()


def list_messages(
    db_path: str,
    room: str,
    after_id: int,
    limit: int,
    agent_id: str | None,
    include_own: bool,
) -> list[dict[str, Any]]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        params: list[Any] = [room, after_id]
        where = ["room = ?", "id > ?"]
        if agent_id:
            # Pull messages addressed to this agent + broadcast (recipient_id is NULL).
            where.append("(recipient_id IS NULL OR recipient_id = ?)")
            params.append(agent_id)
            if not include_own:
                where.append("sender_id != ?")
                params.append(agent_id)

        q = f"""
            SELECT id, room, sender_id, recipient_id, body, created_at
                 , message_kind
                 , response_policy
            FROM messages
            WHERE {' AND '.join(where)}
            ORDER BY id ASC
            LIMIT ?
        """
        params.append(limit)
        rows = conn.execute(q, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_room_state(db_path: str, room: str) -> dict[str, Any] | None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM rooms WHERE room = ?", (room,)).fetchone()
        return None if row is None else room_row_to_dict(row)
    finally:
        conn.close()


def validate_registry_payload(payload: dict[str, Any]) -> dict[str, Any]:
    session_id = normalize_id(str(payload.get("session_id") or "").strip())
    if not session_id:
        raise ValueError("session_id is required")

    room = str(payload.get("room") or DEFAULT_ROOM).strip()
    if not room:
        raise ValueError("room is required")

    status = str(payload.get("status") or "unknown").strip().lower()
    if status not in VALID_REGISTRY_STATUSES:
        raise ValueError(
            f"status must be one of {', '.join(sorted(VALID_REGISTRY_STATUSES))}"
        )

    identities = payload.get("identities") or []
    if not isinstance(identities, list):
        raise ValueError("identities must be a list")
    normalized_identities: list[str] = []
    seen_identities: set[str] = set()
    for identity in identities:
        if identity is None:
            continue
        text = str(identity).strip()
        if not text:
            continue
        if text.lower().startswith(ALIAS_PREFIX):
            alias_to_session_id(text)
            normalized = text.lower()
        else:
            normalized = normalize_id(text)
        if normalized and normalized not in seen_identities:
            normalized_identities.append(normalized)
            seen_identities.add(normalized)

    attached = payload.get("attached", True)
    attached_flag = 1 if bool(attached) else 0
    ephemeral = payload.get("ephemeral")
    ephemeral_flag = None if ephemeral is None else (1 if bool(ephemeral) else 0)

    def optional_text(key: str) -> str | None:
        value = payload.get(key)
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    return {
        "session_id": session_id,
        "room": room,
        "attached": attached_flag,
        "cwd": optional_text("cwd"),
        "repo_name": optional_text("repo_name"),
        "attention_mode": optional_text("attention_mode"),
        "identities_json": json.dumps(normalized_identities),
        "session_kind": optional_text("session_kind"),
        "resumed_from": optional_text("resumed_from"),
        "ephemeral": ephemeral_flag,
        "rollout_path": optional_text("rollout_path"),
        "status": status,
        "role": optional_text("role"),
        "task": optional_text("task"),
        "scope": optional_text("scope"),
    }


def identity_is_session_bound(identity: str) -> bool:
    if not identity:
        return False
    if identity.lower().startswith(ALIAS_PREFIX):
        return True
    try:
        uuid.UUID(identity)
    except ValueError:
        return False
    return True


def logical_identities_from_json(raw: str) -> set[str]:
    try:
        identities = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return set()
    return {
        str(identity).strip().lower()
        for identity in identities
        if isinstance(identity, str)
        and str(identity).strip()
        and not identity_is_session_bound(str(identity).strip())
    }


def assert_unique_attached_logical_identities(
    conn: sqlite3.Connection, session_id: str, attached: int, identities_json: str
) -> None:
    if not attached:
        return
    logical_identities = logical_identities_from_json(identities_json)
    if not logical_identities:
        return
    rows = conn.execute(
        """
        SELECT session_id, identities_json, updated_at, last_heartbeat_at
        FROM registry
        WHERE attached = 1
          AND session_id != ?
        """,
        (session_id,),
    ).fetchall()
    now = datetime.now(timezone.utc)
    for row in rows:
        if not registry_row_is_fresh(row, now):
            continue
        overlap = logical_identities & logical_identities_from_json(row["identities_json"])
        if overlap:
            joined = ", ".join(sorted(overlap))
            raise ValueError(
                f"logical identity already attached by another session: {joined}"
            )


def upsert_registry_entry(db_path: str, payload: dict[str, Any]) -> dict[str, Any]:
    values = validate_registry_payload(payload)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        now = utcnow_iso()
        values["updated_at"] = now
        values["last_heartbeat_at"] = now
        assert_unique_attached_logical_identities(
            conn,
            values["session_id"],
            values["attached"],
            values["identities_json"],
        )
        ensure_room_entry(conn, values["room"])
        conn.execute(
            """
            INSERT INTO registry(
                session_id, room, attached, cwd, repo_name, attention_mode,
                identities_json, session_kind, resumed_from, ephemeral, rollout_path,
                status, role, task, scope, updated_at, last_heartbeat_at
            ) VALUES (
                :session_id, :room, :attached, :cwd, :repo_name, :attention_mode,
                :identities_json, :session_kind, :resumed_from, :ephemeral, :rollout_path,
                :status, :role, :task, :scope, :updated_at, :last_heartbeat_at
            )
            ON CONFLICT(session_id) DO UPDATE SET
                room = excluded.room,
                attached = excluded.attached,
                cwd = excluded.cwd,
                repo_name = excluded.repo_name,
                attention_mode = excluded.attention_mode,
                identities_json = excluded.identities_json,
                session_kind = excluded.session_kind,
                resumed_from = excluded.resumed_from,
                ephemeral = excluded.ephemeral,
                rollout_path = excluded.rollout_path,
                status = excluded.status,
                role = COALESCE(excluded.role, registry.role),
                task = COALESCE(excluded.task, registry.task),
                scope = COALESCE(excluded.scope, registry.scope),
                updated_at = excluded.updated_at,
                last_heartbeat_at = excluded.last_heartbeat_at
            """,
            values,
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM registry WHERE session_id = ?",
            (values["session_id"],),
        ).fetchone()
        return registry_row_to_dict(row)
    finally:
        conn.close()


def registry_row_to_dict(row: sqlite3.Row | None) -> dict[str, Any]:
    if row is None:
        raise ValueError("registry row missing")
    data = dict(row)
    try:
        identities = json.loads(data.pop("identities_json") or "[]")
    except json.JSONDecodeError:
        identities = []
    data["identities"] = identities
    data["attached"] = bool(data.get("attached"))
    if data.get("ephemeral") is not None:
        data["ephemeral"] = bool(data["ephemeral"])
    return data


def list_registry_entries(
    db_path: str,
    room: str,
    limit: int,
) -> list[dict[str, Any]]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT *
            FROM registry
            WHERE room = ?
            ORDER BY last_heartbeat_at DESC, session_id ASC
            LIMIT ?
            """,
            (room, limit),
        ).fetchall()
        return [registry_row_to_dict(row) for row in rows]
    finally:
        conn.close()


def validate_room_payload(payload: dict[str, Any]) -> dict[str, Any]:
    room = str(payload.get("room") or "").strip()
    if not room:
        raise ValueError("room is required")
    state_version = payload.get("state_version")
    if state_version is not None:
        try:
            state_version = int(state_version)
        except (TypeError, ValueError) as exc:
            raise ValueError("state_version must be an integer") from exc
        if state_version < 1:
            raise ValueError("state_version must be >= 1")
    bump_state_version = bool(payload.get("bump_state_version"))
    contract_version = payload.get("contract_version")
    if contract_version is not None:
        contract_version = str(contract_version).strip() or None
    coordination_policy = payload.get("coordination_policy")
    if coordination_policy is not None:
        coordination_policy = str(coordination_policy).strip().lower() or None
        if coordination_policy is not None and coordination_policy not in VALID_COORDINATION_POLICIES:
            raise ValueError(
                "coordination_policy must be one of: "
                + ", ".join(sorted(VALID_COORDINATION_POLICIES))
            )
    coordination_phase = payload.get("coordination_phase")
    if coordination_phase is not None:
        coordination_phase = str(coordination_phase).strip().lower() or None
        if coordination_phase is not None and coordination_phase not in VALID_COORDINATION_PHASES:
            raise ValueError(
                "coordination_phase must be one of: "
                + ", ".join(sorted(VALID_COORDINATION_PHASES))
            )
    coordination_epoch = payload.get("coordination_epoch")
    if coordination_epoch is not None:
        try:
            coordination_epoch = int(coordination_epoch)
        except (TypeError, ValueError) as exc:
            raise ValueError("coordination_epoch must be an integer") from exc
        if coordination_epoch < 1:
            raise ValueError("coordination_epoch must be >= 1")
    leader_session_id = payload.get("leader_session_id")
    if leader_session_id is not None:
        leader_session_id = str(leader_session_id).strip() or None
    verifier_session_id = payload.get("verifier_session_id")
    if verifier_session_id is not None:
        verifier_session_id = str(verifier_session_id).strip() or None
    archived = payload.get("archived")
    archived = bool(archived) if archived is not None else None
    return {
        "room": room,
        "state_version": state_version,
        "bump_state_version": bump_state_version,
        "contract_version": contract_version,
        "coordination_policy": coordination_policy,
        "coordination_phase": coordination_phase,
        "coordination_epoch": coordination_epoch,
        "leader_session_id": leader_session_id,
        "verifier_session_id": verifier_session_id,
        "archived": archived,
    }


def upsert_room_state(db_path: str, payload: dict[str, Any]) -> dict[str, Any]:
    values = validate_room_payload(payload)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        ensure_room_entry(conn, values["room"])
        current = conn.execute("SELECT * FROM rooms WHERE room = ?", (values["room"],)).fetchone()
        if current is None:
            raise ValueError("room row missing")
        current = room_row_to_dict(current)
        if values["state_version"] is not None:
            state_version = values["state_version"]
        elif values["bump_state_version"]:
            state_version = int(current["state_version"]) + 1
        else:
            state_version = int(current["state_version"])
        contract_version = values["contract_version"] or str(current["contract_version"])
        coordination_policy = (
            current["coordination_policy"]
            if payload.get("coordination_policy") is None
            else values["coordination_policy"]
        )
        coordination_phase = (
            current["coordination_phase"]
            if payload.get("coordination_phase") is None
            else values["coordination_phase"]
        )
        coordination_epoch = (
            int(current["coordination_epoch"])
            if values["coordination_epoch"] is None
            else values["coordination_epoch"]
        )
        leader_session_id = (
            current["leader_session_id"]
            if payload.get("leader_session_id") is None
            else values["leader_session_id"]
        )
        verifier_session_id = (
            current["verifier_session_id"]
            if payload.get("verifier_session_id") is None
            else values["verifier_session_id"]
        )
        archived_at = current["archived_at"]
        if values["archived"] is True:
            archived_at = utcnow_iso()
        elif values["archived"] is False:
            archived_at = None
        updated_at = utcnow_iso()
        conn.execute(
            """
            UPDATE rooms
            SET state_version = ?,
                contract_version = ?,
                coordination_policy = ?,
                coordination_phase = ?,
                coordination_epoch = ?,
                leader_session_id = ?,
                verifier_session_id = ?,
                updated_at = ?,
                archived_at = ?
            WHERE room = ?
            """,
            (
                state_version,
                contract_version,
                coordination_policy,
                coordination_phase,
                coordination_epoch,
                leader_session_id,
                verifier_session_id,
                updated_at,
                archived_at,
                values["room"],
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM rooms WHERE room = ?", (values["room"],)).fetchone()
        return room_row_to_dict(row)
    finally:
        conn.close()


def list_rooms(
    db_path: str,
    room: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        if room:
            rows = conn.execute(
                """
                SELECT *
                FROM rooms
                WHERE room = ?
                ORDER BY updated_at DESC, room ASC
                LIMIT ?
                """,
                (room, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT *
                FROM rooms
                ORDER BY updated_at DESC, room ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [room_row_to_dict(row) for row in rows]
    finally:
        conn.close()


def validate_team_create_payload(payload: dict[str, Any]) -> dict[str, Any]:
    team_id = str(payload.get("team_id") or uuid.uuid4()).strip().lower()
    room = str(payload.get("room") or DEFAULT_ROOM).strip()
    purpose = str(payload.get("purpose") or "").strip()
    leader_session_id = normalize_id(str(payload.get("leader_session_id") or "").strip())
    task_room = str(payload.get("task_room") or "").strip() or None
    status = str(payload.get("status") or "active").strip().lower()
    if not room:
        raise ValueError("room is required")
    if not purpose:
        raise ValueError("purpose is required")
    if not leader_session_id:
        raise ValueError("leader_session_id is required")
    members = payload.get("members") or []
    if not isinstance(members, list):
        raise ValueError("members must be a list")
    validated_members: list[dict[str, Any]] = []
    seen: set[str] = set()
    for member in members:
        if not isinstance(member, dict):
            raise ValueError("each member must be an object")
        session_id = normalize_id(str(member.get("session_id") or "").strip())
        if not session_id:
            raise ValueError("member session_id is required")
        if session_id in seen:
            continue
        seen.add(session_id)
        role = str(member.get("role") or "member").strip().lower() or "member"
        state = str(member.get("state") or "pending").strip().lower() or "pending"
        if state not in VALID_TEAM_MEMBER_STATES:
            raise ValueError(
                f"member state must be one of {', '.join(sorted(VALID_TEAM_MEMBER_STATES))}"
            )
        validated_members.append(
            {
                "session_id": session_id,
                "role": role,
                "state": state,
            }
        )
    if leader_session_id not in seen:
        validated_members.insert(
            0,
            {
                "session_id": leader_session_id,
                "role": "leader",
                "state": "active",
            },
        )
    return {
        "team_id": team_id,
        "room": room,
        "task_room": task_room,
        "purpose": purpose,
        "leader_session_id": leader_session_id,
        "status": status,
        "members": validated_members,
    }


def team_member_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def list_team_members(conn: sqlite3.Connection, team_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT team_id, session_id, role, state, joined_room, task, scope,
               invited_by, invited_at, responded_at, updated_at
        FROM team_members
        WHERE team_id = ?
        ORDER BY invited_at ASC, session_id ASC
        """,
        (team_id,),
    ).fetchall()
    return [team_member_row_to_dict(row) for row in rows]


def team_row_to_dict(conn: sqlite3.Connection, row: sqlite3.Row | None) -> dict[str, Any]:
    if row is None:
        raise ValueError("team row missing")
    data = dict(row)
    data["members"] = list_team_members(conn, data["team_id"])
    return data


def create_team(db_path: str, payload: dict[str, Any]) -> dict[str, Any]:
    values = validate_team_create_payload(payload)
    now = utcnow_iso()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        ensure_room_entry(conn, values["room"])
        if values["task_room"]:
            ensure_room_entry(conn, values["task_room"])
        conn.execute(
            """
            INSERT INTO teams(
                team_id, room, task_room, purpose, leader_session_id, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                values["team_id"],
                values["room"],
                values["task_room"],
                values["purpose"],
                values["leader_session_id"],
                values["status"],
                now,
                now,
            ),
        )
        for member in values["members"]:
            responded_at = (
                now
                if member["state"]
                in {"accepted", "joined", "active", "declined", "deferred", "timed_out"}
                else None
            )
            conn.execute(
                """
                INSERT INTO team_members(
                    team_id, session_id, role, state, joined_room, task, scope,
                    invited_by, invited_at, responded_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    values["team_id"],
                    member["session_id"],
                    member["role"],
                    member["state"],
                    values["task_room"],
                    None,
                    None,
                    values["leader_session_id"],
                    now,
                    responded_at,
                    now,
                ),
            )
        conn.commit()
        row = conn.execute("SELECT * FROM teams WHERE team_id = ?", (values["team_id"],)).fetchone()
        return team_row_to_dict(conn, row)
    finally:
        conn.close()


def validate_team_member_payload(payload: dict[str, Any]) -> dict[str, Any]:
    team_id = str(payload.get("team_id") or "").strip().lower()
    session_id = normalize_id(str(payload.get("session_id") or "").strip())
    if not team_id:
        raise ValueError("team_id is required")
    if not session_id:
        raise ValueError("session_id is required")
    state = payload.get("state")
    if state is not None:
        state = str(state).strip().lower()
        if state not in VALID_TEAM_MEMBER_STATES:
            raise ValueError(
                f"state must be one of {', '.join(sorted(VALID_TEAM_MEMBER_STATES))}"
            )

    def optional_text(key: str) -> str | None:
        value = payload.get(key)
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    return {
        "team_id": team_id,
        "session_id": session_id,
        "role": optional_text("role"),
        "state": state,
        "joined_room": optional_text("joined_room"),
        "task": optional_text("task"),
        "scope": optional_text("scope"),
    }


def upsert_team_member(db_path: str, payload: dict[str, Any]) -> dict[str, Any]:
    values = validate_team_member_payload(payload)
    now = utcnow_iso()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        existing_team = conn.execute(
            "SELECT * FROM teams WHERE team_id = ?",
            (values["team_id"],),
        ).fetchone()
        if existing_team is None:
            raise ValueError("team_id not found")
        if values["joined_room"]:
            ensure_room_entry(conn, values["joined_room"])
        current = conn.execute(
            "SELECT * FROM team_members WHERE team_id = ? AND session_id = ?",
            (values["team_id"], values["session_id"]),
        ).fetchone()
        invited_at = current["invited_at"] if current is not None else now
        responded_at = (
            now
            if values["state"] in {"accepted", "joined", "active", "declined", "deferred", "timed_out"}
            else (current["responded_at"] if current is not None else None)
        )
        conn.execute(
            """
            INSERT INTO team_members(
                team_id, session_id, role, state, joined_room, task, scope,
                invited_by, invited_at, responded_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(team_id, session_id) DO UPDATE SET
                role = COALESCE(excluded.role, team_members.role),
                state = COALESCE(excluded.state, team_members.state),
                joined_room = COALESCE(excluded.joined_room, team_members.joined_room),
                task = COALESCE(excluded.task, team_members.task),
                scope = COALESCE(excluded.scope, team_members.scope),
                responded_at = COALESCE(excluded.responded_at, team_members.responded_at),
                updated_at = excluded.updated_at
            """,
            (
                values["team_id"],
                values["session_id"],
                values["role"] or (current["role"] if current is not None else "member"),
                values["state"] or (current["state"] if current is not None else "pending"),
                values["joined_room"] or (current["joined_room"] if current is not None else None),
                values["task"] or (current["task"] if current is not None else None),
                values["scope"] or (current["scope"] if current is not None else None),
                existing_team["leader_session_id"],
                invited_at,
                responded_at,
                now,
            ),
        )
        conn.execute(
            "UPDATE teams SET updated_at = ? WHERE team_id = ?",
            (now, values["team_id"]),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM team_members WHERE team_id = ? AND session_id = ?",
            (values["team_id"], values["session_id"]),
        ).fetchone()
        return team_member_row_to_dict(row)
    finally:
        conn.close()


def list_teams(
    db_path: str,
    room: str,
    limit: int,
) -> list[dict[str, Any]]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT *
            FROM teams
            WHERE room = ?
            ORDER BY updated_at DESC, team_id ASC
            LIMIT ?
            """,
            (room, limit),
        ).fetchall()
        return [team_row_to_dict(conn, row) for row in rows]
    finally:
        conn.close()


def make_handler(db_path: str):
    class Handler(BaseHTTPRequestHandler):
        server_version = SERVICE_VERSION

        def _json(self, code: int, payload: dict[str, Any]) -> None:
            blob = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(blob)))
            self.end_headers()
            self.wfile.write(blob)

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse.urlparse(self.path)

            if parsed.path == f"{API_PREFIX}/health":
                self._json(
                    HTTPStatus.OK,
                    {
                        "ok": True,
                        "time": utcnow_iso(),
                        "service_version": SERVICE_VERSION,
                        "schema_version": SCHEMA_VERSION,
                        "room_contract_version": ROOM_CONTRACT_VERSION,
                    },
                )
                return

            if parsed.path == f"{API_PREFIX}/messages":
                qs = urlparse.parse_qs(parsed.query)
                room = qs.get("room", [DEFAULT_ROOM])[0]
                agent_id = qs.get("agent_id", [None])[0]
                include_own = qs.get("include_own", ["0"])[0] in ("1", "true", "yes")
                try:
                    after_id = int(qs.get("after_id", ["0"])[0])
                    limit = int(qs.get("limit", ["100"])[0])
                except ValueError:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": "after_id and limit must be integers"})
                    return
                try:
                    agent_id = normalize_id(agent_id)
                except ValueError as e:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": str(e)})
                    return

                limit = max(1, min(1000, limit))
                messages = list_messages(db_path, room, after_id, limit, agent_id, include_own)
                last_id = messages[-1]["id"] if messages else after_id
                self._json(
                    HTTPStatus.OK,
                    {
                        "messages": messages,
                        "count": len(messages),
                        "last_id": last_id,
                        "room_state": get_room_state(db_path, room),
                    },
                )
                return

            if parsed.path == f"{API_PREFIX}/registry":
                qs = urlparse.parse_qs(parsed.query)
                room = qs.get("room", [DEFAULT_ROOM])[0]
                try:
                    limit = int(qs.get("limit", ["100"])[0])
                except ValueError:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": "limit must be an integer"})
                    return
                limit = max(1, min(1000, limit))
                entries = list_registry_entries(db_path, room, limit)
                self._json(
                    HTTPStatus.OK,
                    {"entries": entries, "count": len(entries)},
                )
                return

            if parsed.path == f"{API_PREFIX}/teams":
                qs = urlparse.parse_qs(parsed.query)
                room = qs.get("room", [DEFAULT_ROOM])[0]
                try:
                    limit = int(qs.get("limit", ["100"])[0])
                except ValueError:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": "limit must be an integer"})
                    return
                limit = max(1, min(1000, limit))
                teams = list_teams(db_path, room, limit)
                self._json(HTTPStatus.OK, {"teams": teams, "count": len(teams)})
                return

            if parsed.path == f"{API_PREFIX}/rooms":
                qs = urlparse.parse_qs(parsed.query)
                room = qs.get("room", [None])[0]
                try:
                    limit = int(qs.get("limit", ["100"])[0])
                except ValueError:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": "limit must be an integer"})
                    return
                limit = max(1, min(1000, limit))
                rooms = list_rooms(db_path, room, limit)
                self._json(
                    HTTPStatus.OK,
                    {
                        "rooms": rooms,
                        "count": len(rooms),
                        "schema_version": SCHEMA_VERSION,
                        "room_contract_version": ROOM_CONTRACT_VERSION,
                    },
                )
                return

            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            if self.path == f"{API_PREFIX}/rooms":
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid Content-Length"})
                    return

                raw = self.rfile.read(length)
                try:
                    payload = json.loads(raw.decode("utf-8") or "{}")
                except json.JSONDecodeError:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid json"})
                    return

                try:
                    room = upsert_room_state(db_path, payload)
                except ValueError as e:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": str(e)})
                    return
                self._json(HTTPStatus.CREATED, {"ok": True, "room": room})
                return

            if self.path == f"{API_PREFIX}/registry":
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid Content-Length"})
                    return

                raw = self.rfile.read(length)
                try:
                    payload = json.loads(raw.decode("utf-8") or "{}")
                except json.JSONDecodeError:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid json"})
                    return

                try:
                    entry = upsert_registry_entry(db_path, payload)
                except ValueError as e:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": str(e)})
                    return
                self._json(HTTPStatus.CREATED, {"ok": True, "entry": entry})
                return

            if self.path == f"{API_PREFIX}/teams":
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid Content-Length"})
                    return

                raw = self.rfile.read(length)
                try:
                    payload = json.loads(raw.decode("utf-8") or "{}")
                except json.JSONDecodeError:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid json"})
                    return

                try:
                    team = create_team(db_path, payload)
                except ValueError as e:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": str(e)})
                    return
                self._json(HTTPStatus.CREATED, {"ok": True, "team": team})
                return

            if self.path == f"{API_PREFIX}/team-members":
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid Content-Length"})
                    return

                raw = self.rfile.read(length)
                try:
                    payload = json.loads(raw.decode("utf-8") or "{}")
                except json.JSONDecodeError:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid json"})
                    return

                try:
                    member = upsert_team_member(db_path, payload)
                except ValueError as e:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": str(e)})
                    return
                self._json(HTTPStatus.CREATED, {"ok": True, "member": member})
                return

            if self.path != f"{API_PREFIX}/messages":
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return

            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid Content-Length"})
                return

            raw = self.rfile.read(length)
            try:
                payload = json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid json"})
                return

            room = str(payload.get("room") or DEFAULT_ROOM).strip()
            sender_id = str(payload.get("sender_id") or "").strip()
            recipient_id = payload.get("recipient_id")
            message_kind = str(payload.get("message_kind") or "").strip().lower()
            response_policy = str(payload.get("response_policy") or "").strip().lower()
            body = str(payload.get("body") or "").rstrip("\n")

            if not sender_id:
                self._json(HTTPStatus.BAD_REQUEST, {"error": "sender_id is required"})
                return
            if not body:
                self._json(HTTPStatus.BAD_REQUEST, {"error": "body is required"})
                return
            if recipient_id is not None:
                recipient_id = str(recipient_id).strip() or None
            try:
                sender_id = normalize_id(sender_id) or ""
                recipient_id = normalize_id(recipient_id)
            except ValueError as e:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(e)})
                return

            if message_kind:
                if message_kind not in VALID_MESSAGE_KINDS:
                    self._json(
                        HTTPStatus.BAD_REQUEST,
                        {
                            "error": f"message_kind must be one of {', '.join(sorted(VALID_MESSAGE_KINDS))}"
                        },
                    )
                    return
            elif recipient_id:
                message_kind = MESSAGE_KIND_DIRECT
            else:
                message_kind = MESSAGE_KIND_AMBIENT

            if response_policy:
                if response_policy not in VALID_RESPONSE_POLICIES:
                    self._json(
                        HTTPStatus.BAD_REQUEST,
                        {
                            "error": f"response_policy must be one of {', '.join(sorted(VALID_RESPONSE_POLICIES))}"
                        },
                    )
                    return
            elif recipient_id or message_kind == MESSAGE_KIND_DIRECT:
                response_policy = RESPONSE_POLICY_REQUIRED
            elif message_kind == MESSAGE_KIND_BROADCAST:
                response_policy = RESPONSE_POLICY_NONE
            else:
                response_policy = RESPONSE_POLICY_OPTIONAL

            if recipient_id and message_kind == MESSAGE_KIND_BROADCAST:
                self._json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": "broadcast messages cannot also set recipient_id"},
                )
                return

            message = insert_message(
                db_path,
                room,
                sender_id,
                recipient_id,
                message_kind,
                response_policy,
                body,
            )
            self._json(HTTPStatus.CREATED, {"ok": True, "message": message})

        def log_message(self, format: str, *args: Any) -> None:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            sys.stderr.write(f"[{ts}] {self.address_string()} {format % args}\n")

    return Handler


def http_json(method: str, url: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = None
    headers = {}
    if data is not None:
        payload = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urlrequest.Request(url, data=payload, method=method, headers=headers)
    try:
        with urlrequest.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body)
    except urlerror.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise SystemExit(f"HTTP {e.code}: {detail}") from e
    except urlerror.URLError as e:
        raise SystemExit(f"Cannot reach server: {e}") from e


def format_line(msg: dict[str, Any]) -> str:
    sender = display_identity(msg.get("sender_id"))
    to = display_identity(msg.get("recipient_id"))
    kind = msg.get("message_kind") or MESSAGE_KIND_AMBIENT
    response_policy = msg.get("response_policy") or RESPONSE_POLICY_OPTIONAL
    return (
        f"[{msg['id']}] {msg['created_at']} {sender} -> {to} "
        f"[{kind} reply={response_policy}]: {msg['body']}"
    )


def cursor_path(cursor_dir: str, room: str, agent_id: str) -> Path:
    safe_room = room.replace("/", "_")
    safe_agent = agent_id.replace("/", "_")
    return Path(cursor_dir).expanduser() / f"{safe_room}.{safe_agent}.cursor"


def read_cursor(cursor_file: Path) -> int:
    try:
        return int(cursor_file.read_text().strip())
    except Exception:
        return 0


def write_cursor(cursor_file: Path, value: int) -> None:
    cursor_file.parent.mkdir(parents=True, exist_ok=True)
    cursor_file.write_text(str(value))


def cmd_serve(args: argparse.Namespace) -> int:
    init_db(args.db)
    handler = make_handler(args.db)
    server = ThreadingHTTPServer((args.host, args.port), handler)

    print(f"hollywood server listening on http://{args.host}:{args.port}{API_PREFIX}")
    print(f"db: {args.db}")
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def cmd_send(args: argparse.Namespace) -> int:
    text = args.text
    if text is None:
        text = sys.stdin.read().strip("\n")
    try:
        sender_id = normalize_id(args.sender_id)
        to = normalize_id(args.to)
    except ValueError as e:
        raise SystemExit(str(e)) from e

    payload = {
        "room": args.room,
        "sender_id": sender_id,
        "recipient_id": to,
        "message_kind": MESSAGE_KIND_BROADCAST if args.broadcast else None,
        "response_policy": args.response_policy,
        "body": text,
    }
    data = http_json("POST", f"{args.server}{API_PREFIX}/messages", payload)
    msg = data["message"]
    print(format_line(msg))
    return 0


def cmd_register(args: argparse.Namespace) -> int:
    identities = list(args.identity or [])
    if args.session_id not in identities:
        identities.insert(0, args.session_id)
    payload = {
        "session_id": args.session_id,
        "room": args.room,
        "attached": not args.detached,
        "cwd": args.cwd,
        "repo_name": args.repo_name,
        "attention_mode": args.attention_mode,
        "identities": identities,
        "session_kind": args.session_kind,
        "resumed_from": args.resumed_from,
        "ephemeral": args.ephemeral,
        "rollout_path": args.rollout_path,
        "status": args.status,
        "role": args.role,
        "task": args.task,
        "scope": args.scope,
    }
    data = http_json("POST", f"{args.server}{API_PREFIX}/registry", payload)
    print(json.dumps(data["entry"], indent=2, sort_keys=True))
    return 0


def cmd_registry_list(args: argparse.Namespace) -> int:
    url = (
        f"{args.server}{API_PREFIX}/registry?"
        f"{urlparse.urlencode({'room': args.room, 'limit': str(args.limit)})}"
    )
    data = http_json("GET", url)
    if args.json:
        print(json.dumps(data["entries"], indent=2, sort_keys=True))
        return 0
    for entry in data["entries"]:
        print(
            "[{session_id}] room={room} status={status} cwd={cwd} role={role} task={task} scope={scope} updated={updated_at}".format(
                session_id=entry["session_id"],
                room=entry["room"],
                status=entry["status"],
                cwd=entry.get("cwd") or "-",
                role=entry.get("role") or "-",
                task=entry.get("task") or "-",
                scope=entry.get("scope") or "-",
                updated_at=entry["updated_at"],
            )
        )
    return 0


def cmd_team_create(args: argparse.Namespace) -> int:
    members: list[dict[str, Any]] = []
    for spec in args.member or []:
        parts = spec.split(":", 2)
        session_id = parts[0]
        role = parts[1] if len(parts) > 1 and parts[1] else "member"
        state = parts[2] if len(parts) > 2 and parts[2] else "pending"
        members.append(
            {
                "session_id": session_id,
                "role": role,
                "state": state,
            }
        )
    payload = {
        "team_id": args.team_id,
        "room": args.room,
        "task_room": args.task_room,
        "purpose": args.purpose,
        "leader_session_id": args.leader_session_id,
        "status": args.status,
        "members": members,
    }
    data = http_json("POST", f"{args.server}{API_PREFIX}/teams", payload)
    print(json.dumps(data["team"], indent=2, sort_keys=True))
    return 0


def cmd_team_list(args: argparse.Namespace) -> int:
    url = (
        f"{args.server}{API_PREFIX}/teams?"
        f"{urlparse.urlencode({'room': args.room, 'limit': str(args.limit)})}"
    )
    data = http_json("GET", url)
    if args.json:
        print(json.dumps(data["teams"], indent=2, sort_keys=True))
        return 0
    for team in data["teams"]:
        print(
            "[{team_id}] room={room} task_room={task_room} leader={leader} status={status} purpose={purpose}".format(
                team_id=team["team_id"],
                room=team["room"],
                task_room=team.get("task_room") or "-",
                leader=team["leader_session_id"],
                status=team["status"],
                purpose=team["purpose"],
            )
        )
        for member in team.get("members", []):
            print(
                "  - {session_id} role={role} state={state} joined_room={joined_room} task={task} scope={scope}".format(
                    session_id=member["session_id"],
                    role=member["role"],
                    state=member["state"],
                    joined_room=member.get("joined_room") or "-",
                    task=member.get("task") or "-",
                    scope=member.get("scope") or "-",
                )
            )
    return 0


def cmd_team_member_set(args: argparse.Namespace) -> int:
    payload = {
        "team_id": args.team_id,
        "session_id": args.session_id,
        "role": args.role,
        "state": args.state,
        "joined_room": args.joined_room,
        "task": args.task,
        "scope": args.scope,
    }
    data = http_json("POST", f"{args.server}{API_PREFIX}/team-members", payload)
    print(json.dumps(data["member"], indent=2, sort_keys=True))
    return 0


def fetch_messages(
    server: str,
    room: str,
    agent_id: str,
    after_id: int,
    limit: int,
    include_own: bool,
) -> tuple[list[dict[str, Any]], int]:
    qs = {
        "room": room,
        "agent_id": agent_id,
        "after_id": str(after_id),
        "limit": str(limit),
        "include_own": "1" if include_own else "0",
    }
    url = f"{server}{API_PREFIX}/messages?{urlparse.urlencode(qs)}"
    data = http_json("GET", url)
    return data["messages"], int(data["last_id"])


def cmd_poll(args: argparse.Namespace) -> int:
    try:
        agent_id = normalize_id(args.agent_id)
    except ValueError as e:
        raise SystemExit(str(e)) from e
    if not agent_id:
        raise SystemExit("agent_id is required")

    after = args.after_id
    if args.cursor:
        cpath = cursor_path(args.cursor_dir, args.room, agent_id)
        if after is None:
            after = read_cursor(cpath)
    if after is None:
        after = 0

    messages, last_id = fetch_messages(
        args.server,
        args.room,
        agent_id,
        after,
        args.limit,
        args.include_own,
    )
    for msg in messages:
        print(format_line(msg))

    if args.cursor:
        write_cursor(cursor_path(args.cursor_dir, args.room, agent_id), last_id)

    if args.print_last_id:
        print(f"last_id={last_id}")

    return 0


def cmd_tail(args: argparse.Namespace) -> int:
    try:
        agent_id = normalize_id(args.agent_id)
    except ValueError as e:
        raise SystemExit(str(e)) from e
    if not agent_id:
        raise SystemExit("agent_id is required")

    cpath = cursor_path(args.cursor_dir, args.room, agent_id)
    if args.from_now:
        # Move cursor to latest immediately, then print future messages only.
        _, last_id = fetch_messages(args.server, args.room, agent_id, 0, 1_000, args.include_own)
        write_cursor(cpath, last_id)

    after = read_cursor(cpath) if args.cursor else 0

    while True:
        messages, last_id = fetch_messages(
            args.server,
            args.room,
            agent_id,
            after,
            args.limit,
            args.include_own,
        )
        for msg in messages:
            print(format_line(msg), flush=True)

        if args.cursor:
            write_cursor(cpath, last_id)
        after = last_id
        time.sleep(args.interval)


def cmd_alias_encode(args: argparse.Namespace) -> int:
    try:
        print(session_id_to_alias(args.session_id))
    except Exception as e:
        raise SystemExit(f"invalid session_id: {e}") from e
    return 0


def cmd_alias_decode(args: argparse.Namespace) -> int:
    try:
        print(alias_to_session_id(args.alias))
    except Exception as e:
        raise SystemExit(f"invalid alias: {e}") from e
    return 0


def cmd_room_name(args: argparse.Namespace) -> int:
    if args.kind == ROOM_KIND_REPO:
        print(repo_room_name(args.name))
        return 0
    if args.kind == ROOM_KIND_TASK:
        if not args.repo:
            raise SystemExit("--repo is required for task rooms")
        print(task_room_name(args.repo, args.name))
        return 0
    if args.kind == ROOM_KIND_MULTI:
        print(multi_room_name(args.name))
        return 0
    if args.kind == ROOM_KIND_ORG:
        print(org_room_name(args.name))
        return 0
    if args.kind == ROOM_KIND_MAIN:
        print(ROOM_KIND_MAIN)
        return 0
    raise SystemExit(f"unsupported room kind: {args.kind}")


def cmd_room_list(args: argparse.Namespace) -> int:
    params = {"limit": str(args.limit)}
    if args.room:
        params["room"] = args.room
    url = f"{args.server}{API_PREFIX}/rooms?{urlparse.urlencode(params)}"
    data = http_json("GET", url)
    if args.json:
        print(json.dumps(data["rooms"], indent=2, sort_keys=True))
        return 0
    for room in data["rooms"]:
        print(
            "[{room}] kind={kind} repo={repo} task={task} state_version={state_version} contract_version={contract_version} last_message_id={last_message_id} updated={updated_at}".format(
                room=room["room"],
                kind=room["kind"],
                repo=room.get("repo") or "-",
                task=room.get("task") or "-",
                state_version=room["state_version"],
                contract_version=room["contract_version"],
                last_message_id=room["last_message_id"],
                updated_at=room["updated_at"],
            )
        )
    return 0


def cmd_room_set(args: argparse.Namespace) -> int:
    if args.archived and args.unarchive:
        raise SystemExit("choose only one of --archived or --unarchive")
    payload = {
        "room": args.room,
        "state_version": args.state_version,
        "bump_state_version": args.bump_state_version,
        "contract_version": args.contract_version,
        "archived": True if args.archived else (False if args.unarchive else None),
    }
    data = http_json("POST", f"{args.server}{API_PREFIX}/rooms", payload)
    print(json.dumps(data["room"], indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hollywood", description="Local pull-based chatroom for CLI agents")
    sub = parser.add_subparsers(dest="cmd", required=True)

    serve = sub.add_parser("serve", help="Run the local Hollywood server")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--db", default=DEFAULT_DB)
    serve.set_defaults(func=cmd_serve)

    send = sub.add_parser("send", help="Send a message")
    send.add_argument("--server", default=DEFAULT_URL)
    send.add_argument("--room", default=DEFAULT_ROOM)
    send.add_argument("--sender-id", required=True)
    send.add_argument("--to", default=None, help="recipient agent/session id (omit for broadcast)")
    send.add_argument(
        "--broadcast",
        action="store_true",
        help="mark the message as an explicit room-wide broadcast that should wake idle listeners",
    )
    send.add_argument(
        "--response-policy",
        choices=sorted(VALID_RESPONSE_POLICIES),
        default=None,
        help="whether a reply is required, optional, or explicitly not needed",
    )
    send.add_argument("--text", default=None, help="message body; if omitted, reads stdin")
    send.set_defaults(func=cmd_send)

    poll = sub.add_parser("poll", help="Fetch new messages once")
    poll.add_argument("--server", default=DEFAULT_URL)
    poll.add_argument("--room", default=DEFAULT_ROOM)
    poll.add_argument("--agent-id", required=True)
    poll.add_argument("--after-id", type=int, default=None)
    poll.add_argument("--limit", type=int, default=100)
    poll.add_argument("--include-own", action="store_true")
    poll.add_argument("--cursor", action="store_true", help="read/write cursor file for this agent+room")
    poll.add_argument("--cursor-dir", default=DEFAULT_CURSOR_DIR)
    poll.add_argument("--print-last-id", action="store_true")
    poll.set_defaults(func=cmd_poll)

    tail = sub.add_parser("tail", help="Continuously print new messages")
    tail.add_argument("--server", default=DEFAULT_URL)
    tail.add_argument("--room", default=DEFAULT_ROOM)
    tail.add_argument("--agent-id", required=True)
    tail.add_argument("--limit", type=int, default=100)
    tail.add_argument("--interval", type=float, default=1.5)
    tail.add_argument("--include-own", action="store_true")
    tail.add_argument("--cursor", action="store_true", help="read/write cursor file for this agent+room")
    tail.add_argument("--cursor-dir", default=DEFAULT_CURSOR_DIR)
    tail.add_argument("--from-now", action="store_true", help="ignore history and start at latest message")
    tail.set_defaults(func=cmd_tail)

    alias_encode = sub.add_parser("alias-encode", help="Convert UUID session id to deterministic alias")
    alias_encode.add_argument("--session-id", required=True)
    alias_encode.set_defaults(func=cmd_alias_encode)

    alias_decode = sub.add_parser("alias-decode", help="Convert deterministic alias back to UUID session id")
    alias_decode.add_argument("--alias", required=True)
    alias_decode.set_defaults(func=cmd_alias_decode)

    room_name = sub.add_parser("room-name", help="Generate a recommended typed room name")
    room_name.add_argument(
        "--kind",
        choices=[ROOM_KIND_MAIN, ROOM_KIND_REPO, ROOM_KIND_TASK, ROOM_KIND_MULTI, ROOM_KIND_ORG],
        required=True,
    )
    room_name.add_argument(
        "--name",
        default="",
        help="base name for repo, task, multi, or org rooms; ignored for --kind main",
    )
    room_name.add_argument(
        "--repo",
        default=None,
        help="repo name used as the middle segment for --kind task",
    )
    room_name.set_defaults(func=cmd_room_name)

    room_list = sub.add_parser("room-list", help="List persisted room state metadata")
    room_list.add_argument("--server", default=DEFAULT_URL)
    room_list.add_argument("--room", default=None)
    room_list.add_argument("--limit", type=int, default=100)
    room_list.add_argument("--json", action="store_true")
    room_list.set_defaults(func=cmd_room_list)

    room_set = sub.add_parser("room-set", help="Update persisted room state metadata")
    room_set.add_argument("--server", default=DEFAULT_URL)
    room_set.add_argument("--room", required=True)
    room_set.add_argument("--state-version", type=int, default=None)
    room_set.add_argument("--bump-state-version", action="store_true")
    room_set.add_argument("--contract-version", default=None)
    room_set.add_argument("--archived", action="store_true")
    room_set.add_argument("--unarchive", action="store_true")
    room_set.set_defaults(func=cmd_room_set)

    register = sub.add_parser("register", help="Upsert a structured registry entry")
    register.add_argument("--server", default=DEFAULT_URL)
    register.add_argument("--room", default=DEFAULT_ROOM)
    register.add_argument("--session-id", required=True)
    register.add_argument("--cwd", default=None)
    register.add_argument("--repo-name", default=None)
    register.add_argument("--attention-mode", default=None)
    register.add_argument("--identity", action="append", default=None)
    register.add_argument("--session-kind", default=None)
    register.add_argument("--resumed-from", default=None)
    register.add_argument("--ephemeral", action="store_true")
    register.add_argument("--rollout-path", default=None)
    register.add_argument("--status", default="unknown")
    register.add_argument("--role", default=None)
    register.add_argument("--task", default=None)
    register.add_argument("--scope", default=None)
    register.add_argument("--detached", action="store_true")
    register.set_defaults(func=cmd_register)

    registry_list = sub.add_parser("registry-list", help="List structured registry entries")
    registry_list.add_argument("--server", default=DEFAULT_URL)
    registry_list.add_argument("--room", default=DEFAULT_ROOM)
    registry_list.add_argument("--limit", type=int, default=100)
    registry_list.add_argument("--json", action="store_true")
    registry_list.set_defaults(func=cmd_registry_list)

    team_create = sub.add_parser("team-create", help="Create a structured team")
    team_create.add_argument("--server", default=DEFAULT_URL)
    team_create.add_argument("--room", default=DEFAULT_ROOM)
    team_create.add_argument("--task-room", default=None)
    team_create.add_argument("--team-id", default=None)
    team_create.add_argument("--purpose", required=True)
    team_create.add_argument("--leader-session-id", required=True)
    team_create.add_argument(
        "--member",
        action="append",
        default=None,
        help="member spec as session_id[:role[:state]]",
    )
    team_create.add_argument("--status", default="active")
    team_create.set_defaults(func=cmd_team_create)

    team_list = sub.add_parser("team-list", help="List structured teams")
    team_list.add_argument("--server", default=DEFAULT_URL)
    team_list.add_argument("--room", default=DEFAULT_ROOM)
    team_list.add_argument("--limit", type=int, default=100)
    team_list.add_argument("--json", action="store_true")
    team_list.set_defaults(func=cmd_team_list)

    team_member_set = sub.add_parser("team-member-set", help="Update team member state")
    team_member_set.add_argument("--server", default=DEFAULT_URL)
    team_member_set.add_argument("--team-id", required=True)
    team_member_set.add_argument("--session-id", required=True)
    team_member_set.add_argument("--role", default=None)
    team_member_set.add_argument("--state", default=None)
    team_member_set.add_argument("--joined-room", default=None)
    team_member_set.add_argument("--task", default=None)
    team_member_set.add_argument("--scope", default=None)
    team_member_set.set_defaults(func=cmd_team_member_set)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
