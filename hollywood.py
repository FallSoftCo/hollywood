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
from datetime import datetime, timezone
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
ALIAS_PREFIX = "sid-"


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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


def init_db(db_path: str) -> None:
    ensure_parent(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                room TEXT NOT NULL,
                sender_id TEXT NOT NULL,
                recipient_id TEXT,
                body TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_room_id ON messages(room, id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_recipient ON messages(recipient_id)")
        conn.commit()
    finally:
        conn.close()


def insert_message(db_path: str, room: str, sender_id: str, recipient_id: str | None, body: str) -> dict[str, Any]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        created_at = utcnow_iso()
        cur = conn.execute(
            "INSERT INTO messages(room, sender_id, recipient_id, body, created_at) VALUES (?, ?, ?, ?, ?)",
            (room, sender_id, recipient_id, body, created_at),
        )
        conn.commit()
        msg_id = cur.lastrowid
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


def make_handler(db_path: str):
    class Handler(BaseHTTPRequestHandler):
        server_version = "hollywood/0.1"

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
                self._json(HTTPStatus.OK, {"ok": True, "time": utcnow_iso()})
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
                self._json(HTTPStatus.OK, {"messages": messages, "count": len(messages), "last_id": last_id})
                return

            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
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

            message = insert_message(db_path, room, sender_id, recipient_id, body)
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
    return f"[{msg['id']}] {msg['created_at']} {sender} -> {to}: {msg['body']}"


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
        "body": text,
    }
    data = http_json("POST", f"{args.server}{API_PREFIX}/messages", payload)
    msg = data["message"]
    print(format_line(msg))
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

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
