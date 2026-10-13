"""Exact, owner-reviewed supersessions applied before retrying retired budget failures."""

import hashlib
import json
import time
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

RETIRED_ERROR = "Automatic turn budget reached; human review required"


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int = Field(ge=1)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class Supersession(BaseModel):
    model_config = ConfigDict(extra="forbid")
    deliveryId: int = Field(ge=1)
    eventId: int = Field(ge=1)
    task: str = Field(pattern=r"^[a-z0-9_-]{1,64}$")
    agent: str = Field(pattern=r"^[a-z0-9_-]{1,64}$")
    sourceSha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    evidence: list[Evidence] = Field(min_length=1, max_length=20)
    reason: str = Field(min_length=1, max_length=700)


class Plan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1, le=1)
    supersessions: list[Supersession] = Field(default_factory=list, max_length=100)
    retireDeliveryAlerts: list[Evidence] = Field(default_factory=list, max_length=100)


def event_fingerprint(event):
    return hashlib.sha256(
        json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def apply_supersessions(store, path):
    """Caller owns the DB transaction; any mismatch aborts before normal retry migration."""
    path = Path(path)
    if not path.exists():
        return
    with path.open("rb") as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError("Supersession plan exceeds 64 KiB")
    plan = Plan.model_validate_json(raw)
    for entry in plan.supersessions:
        rows = store.rows("SELECT * FROM delivery WHERE id=?", (entry.deliveryId,))
        if not rows:
            raise ValueError("Supersession delivery not found")
        delivery = rows[0]
        if (delivery["event"], delivery["task"], delivery["agent"]) != (
            entry.eventId,
            entry.task,
            entry.agent,
        ):
            raise ValueError("Supersession delivery identity mismatch")
        source = store.get_event(entry.eventId)
        if (
            source["task"] != entry.task
            or source["visibility"] != "room"
            or event_fingerprint(source) != entry.sourceSha256
        ):
            raise ValueError("Supersession source evidence mismatch")
        for expected in entry.evidence:
            evidence = store.get_event(expected.id)
            if (
                evidence["task"] != entry.task
                or evidence["visibility"] != "room"
                or evidence["id"] <= source["id"]
                or event_fingerprint(evidence) != expected.sha256
            ):
                raise ValueError("Supersession completion evidence mismatch")
        plan_hash = hashlib.sha256(entry.model_dump_json().encode()).hexdigest()
        if delivery["state"] == "superseded":
            if not store.rows(
                "SELECT id FROM event WHERE kind='deliverySuperseded' AND json_extract(data,'$.delivery')=? AND json_extract(data,'$.planSha256')=?",
                (entry.deliveryId, plan_hash),
            ):
                raise ValueError("Superseded delivery lacks matching audit receipt")
            continue
        if (
            delivery["state"] != "uncertain"
            or delivery["turn"] is not None
            or delivery["error"] != RETIRED_ERROR
        ):
            raise ValueError("Supersession requires the exact undispatched retired-budget failure")
        store.db.execute("UPDATE delivery SET state='superseded' WHERE id=?", (entry.deliveryId,))
        store.event(
            "deliverySuperseded",
            entry.task,
            "hollywood",
            "Obsolete delivery superseded",
            {
                "delivery": entry.deliveryId,
                "source": entry.eventId,
                "evidence": [item.id for item in entry.evidence],
                "reason": entry.reason,
                "previousError": delivery["error"],
                "planSha256": plan_hash,
            },
        )

    for expected in plan.retireDeliveryAlerts:
        source = store.get_event(expected.id)
        rows = store.rows("SELECT * FROM attention WHERE id=?", (expected.id,))
        if (
            not rows
            or source["kind"] != "attention"
            or event_fingerprint(source) != expected.sha256
            or not (
                source["body"].startswith("Could not confirm delivery: ")
                or isinstance(source["data"].get("deliveryFailure"), dict)
            )
        ):
            raise ValueError("Delivery-alert retirement evidence mismatch")
        if store.rows(
            "SELECT id FROM event WHERE kind='deliveryAlertRetired' AND json_extract(data,'$.attention')=? AND json_extract(data,'$.sourceSha256')=?",
            (expected.id, expected.sha256),
        ):
            continue
        # This clears a retired status notice, not an answer or the underlying work.
        store.db.execute(
            "UPDATE attention SET state='resolved',response=? WHERE id=? AND state='open'",
            ("Delivery-status alert retired; diagnostic delivery state retained", expected.id),
        )
        store.db.execute(
            "INSERT OR IGNORE INTO attention_dismissal VALUES(?,?)", (expected.id, int(time.time()))
        )
        store.event(
            "deliveryAlertRetired",
            source["task"],
            "hollywood",
            "Delivery-status alert retired",
            {
                "attention": expected.id,
                "sourceSha256": expected.sha256,
                "reason": "Delivery uncertainty is internal status, not a human question",
            },
            visibility=source["visibility"],
        )
