"""Principal-scoped communication hints. Reads never admit or wake execution."""

from .attention import human_owner
from .store import Conflict, Denied, json_value


KINDS = ("message", "result", "question", "approval")


def hint(store, c, actor, event):
    room = store.room(c, actor, event["room"])
    attention = c.execute(
        "SELECT q.*,d.state AS delivery_state,d.revision AS delivery_revision FROM hb_attention q JOIN hb_delivery d ON d.id=q.delivery WHERE q.event=%s",
        (event["id"],),
    ).fetchone()
    notify = False
    reason = "room_update"
    revision = 1
    attention_id = None
    if attention:
        revision = attention["revision"] + attention["delivery_revision"]
        attention_id = attention["id"]
        if attention["human"] == actor["id"]:
            if attention["state"] == "pending" and attention["delivery_state"] in ("claimed", "running"):
                notify, reason = True, event["kind"]
            elif attention["state"] == "unknown" or attention["delivery_state"] == "unknown":
                notify, reason = True, "unknown_delivery"
            else:
                reason = "attention_resolved"
    elif event["author"] != actor["id"]:
        if event["recipient"] == actor["id"]:
            notify, reason = True, "direct_message" if event["kind"] == "message" else "work_result"
        elif event["source"]:
            try:
                owner = human_owner(c, event)
            except Conflict:
                owner = None
            if owner == actor["id"]:
                notify, reason = True, "work_result" if event["kind"] == "result" else "work_message"
    return json_value({
        "eventId": event["id"], "sequence": event["seq"], "authorityId": event["authority"],
        "principalId": actor["id"], "roomId": event["room"], "roomName": room["name"],
        "kind": event["kind"], "reason": reason, "notify": notify, "revision": revision,
        "attentionId": attention_id, "createdAt": event["created_at"],
    })


def feed(store, actor_id, after, limit, pending=False):
    with store.pool.connection() as c:
        actor = store.actor(c, actor_id)
        ceiling = c.execute("SELECT next_seq FROM hb_authority WHERE id=%s", (actor["authority"],)).fetchone()["next_seq"]
        if after is not None and after > ceiling:
            raise Conflict("Inbox cursor exceeds retained authority history; reconcile the installation")
        if after is None:
            return {"authorityId": str(actor["authority"]), "principalId": str(actor["id"]), "events": [], "nextCursor": ceiling, "head": ceiling}
        rows = c.execute(
            """SELECT e.* FROM hb_event e
             WHERE e.authority=%s AND e.seq>%s AND e.seq<=%s
             AND e.kind=ANY(%s)
             AND (%s OR EXISTS(SELECT 1 FROM hb_membership m WHERE m.room=e.room AND m.principal=%s AND m.can_read))
             AND (%s OR e.audience='room' OR e.author=%s OR e.recipient=%s)
             AND (NOT %s OR EXISTS(SELECT 1 FROM hb_attention q WHERE q.event=e.id AND q.human=%s AND q.state IN ('pending','unknown')))
             ORDER BY e.seq LIMIT %s""",
            (actor["authority"], after, ceiling, list(KINDS), actor["administrator"], actor["id"],
             actor["administrator"], actor["id"], actor["id"], pending, actor["id"], limit),
        ).fetchall()
        return {"authorityId": str(actor["authority"]), "principalId": str(actor["id"]),
                "events": [hint(store, c, actor, e) for e in rows],
                "nextCursor": rows[-1]["seq"] if len(rows) == limit else ceiling, "head": ceiling}


def read(store, actor_id, identity):
    with store.pool.connection() as c:
        actor = store.actor(c, actor_id)
        event = store.readable_event(c, actor, identity)
        if event["kind"] not in KINDS:
            raise Denied("Event is not a communication inbox item")
        return {"hint": hint(store, c, actor, event), "event": json_value(event)}
