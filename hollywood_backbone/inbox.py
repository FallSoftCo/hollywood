"""Principal-scoped communication hints. Reads never admit or wake execution."""

from .store import Conflict, Denied, json_value
from .human_continuations import can_respond, nonblocking


KINDS = ("message", "result", "question", "approval")
NOTIFICATION_POLICY_REVISION = 2


def hint(store, c, actor, event):
    room = store.room(c, actor, event["room"])
    attention = c.execute(
        "SELECT q.*,d.state AS delivery_state,d.revision AS delivery_revision FROM hb_attention q JOIN hb_delivery d ON d.id=q.delivery WHERE q.event=%s",
        (event["id"],),
    ).fetchone()
    notify = False
    reason = "room_update"
    revision = NOTIFICATION_POLICY_REVISION
    attention_id = None
    if attention:
        # Ending an inference slice is not a new human question.
        revision = attention["revision"] + NOTIFICATION_POLICY_REVISION
        if not nonblocking(attention):
            revision += attention["delivery_revision"]
        attention_id = attention["id"]
        if attention["human"] == actor["id"]:
            delivery = c.execute("SELECT * FROM hb_delivery WHERE id=%s", (attention["delivery"],)).fetchone()
            if can_respond(store, c, actor, attention, delivery):
                notify, reason = True, event["kind"]
            elif attention["state"] == "unknown" or attention["delivery_state"] == "unknown":
                notify, reason = True, "unknown_delivery"
            else:
                reason = "attention_resolved"
    elif event["author"] != actor["id"]:
        if event["audience"] == "direct" and event["recipient"] == actor["id"]:
            notify, reason = True, "direct_message" if event["kind"] == "message" else "work_result"
        elif event["source"] and event["kind"] == "result":
            # A human's request has one terminal response. Delegated descendants
            # and intermediate contributions stay in rooms; ancestry is not an
            # instruction to interrupt the human for every agent's activity.
            source = c.execute(
                "SELECT author,kind FROM hb_event WHERE id=%s AND authority=%s",
                (event["source"], event["authority"]),
            ).fetchone()
            if source and source["author"] == actor["id"] and source["kind"] == "request":
                notify, reason = True, "request_result"
            else:
                reason = "agent_work_update"
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
