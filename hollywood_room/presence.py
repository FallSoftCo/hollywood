"""Expiring device observations, independent of messages and attention receipts."""

import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, StrictBool

TTL_SECONDS = 90


class PresenceReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    environment: Literal["android", "desktop", "web"]
    state: Literal["foreground", "background"]
    suppressForegroundPush: StrictBool | None = None


def ensure_schema(store):
    with store.db:
        store.db.execute("""CREATE TABLE IF NOT EXISTS device_presence (
            device TEXT PRIMARY KEY REFERENCES device(id) ON DELETE CASCADE,
            environment TEXT NOT NULL, state TEXT NOT NULL,
            received_at INTEGER NOT NULL, expires_at INTEGER NOT NULL,
            suppress_foreground_push INTEGER NOT NULL DEFAULT 1
        )""")


def report(store, device, value, *, now=None):
    """Call only behind the authenticated API boundary; trust only server time."""
    now = int(time.time()) if now is None else now
    if not store.rows("SELECT id FROM device WHERE id=? AND revoked=0", (device,)):
        raise ValueError("Presence requires a registered, non-revoked device")
    with store.db:
        store.db.execute(
            """INSERT INTO device_presence
            (device,environment,state,received_at,expires_at,suppress_foreground_push)
            VALUES(?,?,?,?,?,COALESCE(?,1)) ON CONFLICT(device) DO UPDATE SET
            environment=excluded.environment,state=excluded.state,
            received_at=excluded.received_at,expires_at=excluded.expires_at,
            suppress_foreground_push=COALESCE(?,device_presence.suppress_foreground_push)""",
            (
                device,
                value.environment,
                value.state,
                now,
                now + TTL_SECONDS,
                value.suppressForegroundPush,
                value.suppressForegroundPush,
            ),
        )
    return device_state(store, device, now=now)


def device_state(store, device, *, now=None):
    now = int(time.time()) if now is None else now
    rows = store.rows(
        """SELECT p.*,d.revoked FROM device d LEFT JOIN device_presence p ON p.device=d.id
        WHERE d.id=?""",
        (device,),
    )
    if not rows:
        raise ValueError("Unknown device")
    row = rows[0]
    fresh = (
        not row["revoked"]
        and row["received_at"] is not None
        and row["received_at"] <= now < row["expires_at"]
    )
    return {
        "id": device,
        "environment": row["environment"],
        "state": row["state"] if fresh else "unknown",
        "receivedAt": row["received_at"],
        "expiresAt": row["expires_at"],
        "suppressForegroundPush": bool(row["suppress_foreground_push"])
        if row["suppress_foreground_push"] is not None
        else True,
    }


def suppress_push(store, device, *, now=None):
    state = device_state(store, device, now=now)
    return state["state"] == "foreground" and state["suppressForegroundPush"]


def agent_context(store, *, now=None):
    """Aggregate only fixed vocabulary; no tokens, device IDs, or heartbeat events."""
    states = set()
    for row in store.rows("SELECT id FROM device WHERE revoked=0"):
        value = device_state(store, row["id"], now=now)
        states.add((value["environment"] or "unspecified", value["state"]))
    summary = ", ".join(f"{environment} {state}" for environment, state in sorted(states))
    return "Device presence (expires after 90s; not a read receipt): " + (summary or "unknown")
