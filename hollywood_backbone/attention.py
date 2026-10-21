"""Durable human controls scoped to one native execution; no model/runtime imports."""

from psycopg.types.json import Jsonb

from .store import Conflict, Denied, digest, json_value
from . import human_continuations as continuations

TERMINAL = {"completed", "failed", "cancelled"}


def scope(row, delivery):
    return json_value(
        {
            "authorityId": row["authority"],
            "attentionId": row["id"],
            "deliveryId": row["delivery"],
            "agentId": row["agent"],
            "roomId": row["room"],
            "hostId": delivery["host"],
            "generation": row["generation"],
            "nativeThread": row["native_thread"],
            "nativeTurn": row["native_turn"],
            "requestDigest": row["digest"],
        }
    )


def human_owner(c, event):
    """Resolve the attributable human through accepted delegation, never a caller hint."""
    visited = set()
    for _ in range(64):
        if event["id"] in visited:
            raise Conflict("Delegation source cycle")
        visited.add(event["id"])
        if event["kind"] == "control" and event["data"].get("type") == "roleWork":
            # Its source is evidence, not delegated human authority. Until an
            # explicit role decision owner is enrolled, never select a human
            # by following the triggering fact's conversation lineage.
            raise Conflict("Role work needs an explicitly enrolled human decision owner; evidence is not approval authority")
        actor = c.execute(
            "SELECT * FROM hb_principal WHERE id=%s AND authority=%s AND active",
            (event["author"], event["authority"]),
        ).fetchone()
        if actor and actor["kind"] == "human":
            return actor["id"]
        if not event["source"]:
            raise Conflict("Question/approval lacks an attributable human request")
        event = c.execute(
            "SELECT * FROM hb_event WHERE id=%s AND authority=%s",
            (event["source"], event["authority"]),
        ).fetchone()
        if not event:
            raise Conflict("Delegation source unavailable")
    raise Conflict("Delegation source depth exceeded")


def owned_delivery(store, c, actor, identity, claim, generation):
    d = c.execute(
        "SELECT * FROM hb_delivery WHERE id=%s AND agent=%s AND authority=%s FOR UPDATE",
        (identity, actor["id"], actor["authority"]),
    ).fetchone()
    if actor["kind"] != "agent" or not d:
        raise Denied("Native execution owner required")
    if d["claim_id"] != claim or d["generation"] != generation:
        raise Conflict("Native execution ownership changed")
    return d


def receipt(store, c, actor, row, delivery):
    store.readable_event(c, actor, row["event"])
    if not actor["administrator"] and actor["id"] not in (row["human"], row["agent"]):
        raise Denied("Attention participant required")
    return {
        "attention": json_value(row),
        "scope": scope(row, delivery),
        "deliveryState": delivery["state"],
        "nativeConsumptionProved": False,
        "canRespond": continuations.can_respond(store, c, actor, row, delivery),
        "responseMode": "same-agent-context-input" if continuations.nonblocking(row) else "native-callback",
        "continuation": json_value(continuations.continuation(c, row)),
    }


def create(store, actor_id, identity, payload):
    with store.pool.connection() as c:
        a = store.actor(c, actor_id)
        d = owned_delivery(
            store, c, a, payload["deliveryId"], payload["claimId"], payload["generation"]
        )
        fingerprint = digest(
            {
                "method": payload["method"],
                "request": payload["request"],
                "summary": payload["summary"],
            }
        )
        old = c.execute("SELECT * FROM hb_attention WHERE id=%s FOR UPDATE", (identity,)).fetchone()
        if old:
            if (
                old["authority"],
                old["delivery"],
                old["agent"],
                old["generation"],
                old["native_thread"],
                old["native_turn"],
                old["native_slot"],
                old["digest"],
            ) != (
                a["authority"],
                d["id"],
                a["id"],
                d["generation"],
                payload["nativeThread"],
                payload["nativeTurn"],
                payload["nativeSlot"],
                fingerprint,
            ):
                raise Conflict("Attention identity changed")
            return receipt(store, c, a, old, d)
        if d["state"] not in ("claimed", "running"):
            raise Conflict("Native turn no longer accepts attention")
        if c.execute("SELECT 1 FROM hb_context_system WHERE agent=%s AND enabled",(a["id"],)).fetchone():
            if not c.execute("""SELECT 1 FROM hb_purpose_context b JOIN hb_context_permit p
                ON p.agent=b.agent AND p.purpose=b.purpose AND p.native_thread=b.native_thread
                AND p.native_turn=b.active_turn AND p.state='running'
                WHERE b.agent=%s AND b.purpose='writer' AND b.accepting
                  AND b.active_turn=%s AND b.native_thread=%s""",
                (a["id"],payload["nativeTurn"],payload["nativeThread"])).fetchone():
                raise Conflict("Current admitted purpose writer required for new attention")
        if (d["native_thread"] and d["native_thread"] != payload["nativeThread"]) or (
            d["native_turn"] and d["native_turn"] != payload["nativeTurn"]
        ):
            raise Conflict("Attention belongs to another native turn")
        event = store.readable_event(c, a, d["event"])
        store.room(c, a, event["room"], "can_post")
        human = human_owner(c, event)
        h = store.actor(c, human)
        store.room(c, h, event["room"])
        # Approval detail is private to the attributable human and agent even
        # when the original work request was published to the entire room.
        kind = (
            "question"
            if payload["method"] in ("hollywood/question", "item/tool/requestUserInput")
            else "approval"
        )
        eid = store.event(
            c,
            a,
            event["room"],
            kind,
            payload["summary"],
            recipient=human,
            audience="direct",
            source=event["id"],
            data={
                "attentionId": identity,
                "deliveryId": str(d["id"]),
                "requiresResponse": True,
                "state": "pending",
            },
        )
        c.execute(
            "UPDATE hb_delivery SET native_thread=COALESCE(native_thread,%s),native_turn=COALESCE(native_turn,%s) WHERE id=%s",
            (payload["nativeThread"], payload["nativeTurn"], d["id"]),
        )
        c.execute(
            """INSERT INTO hb_attention(id,authority,delivery,agent,human,room,event,generation,claim_id,
                  native_thread,native_turn,native_slot,method,request,digest)
                  VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                identity,
                a["authority"],
                d["id"],
                a["id"],
                human,
                event["room"],
                eid,
                d["generation"],
                d["claim_id"],
                payload["nativeThread"],
                payload["nativeTurn"],
                payload["nativeSlot"],
                payload["method"],
                Jsonb(payload["request"]),
                fingerprint,
            ),
        )
        row = c.execute("SELECT * FROM hb_attention WHERE id=%s", (identity,)).fetchone()
        if continuations.nonblocking(row):
            continuations.context(c, row)
        return receipt(store, c, a, row, d)


def read(store, actor_id, identity):
    with store.pool.connection() as c:
        a = store.actor(c, actor_id)
        row = c.execute(
            "SELECT * FROM hb_attention WHERE id=%s AND authority=%s", (identity, a["authority"])
        ).fetchone()
        if not row:
            raise Denied("Attention unavailable")
        d = c.execute("SELECT * FROM hb_delivery WHERE id=%s", (row["delivery"],)).fetchone()
        return receipt(store, c, a, row, d)


def inbox(store, actor_id, room, after, limit):
    with store.pool.connection() as c:
        a = store.actor(c, actor_id)
        store.room(c, a, room)
        rows = c.execute(
            """SELECT q.* FROM hb_attention q JOIN hb_event e ON e.id=q.event
                         WHERE q.authority=%s AND q.room=%s AND e.seq>%s
                         AND (%s OR q.human=%s OR q.agent=%s) ORDER BY e.seq LIMIT %s""",
            (a["authority"], room, after, a["administrator"], a["id"], a["id"], limit),
        ).fetchall()
        out = []
        cursor = after
        for row in rows:
            d = c.execute("SELECT * FROM hb_delivery WHERE id=%s", (row["delivery"],)).fetchone()
            value = receipt(store, c, a, row, d)
            cursor = c.execute("SELECT seq FROM hb_event WHERE id=%s", (row["event"],)).fetchone()[
                "seq"
            ]
            out.append(value)
        return {"attention": out, "nextCursor": cursor}


def respond(store, actor_id, identity, operation, payload):
    with store.pool.connection() as c:
        a = store.actor(c, actor_id)
        ref = c.execute(
            "SELECT delivery FROM hb_attention WHERE id=%s AND authority=%s",
            (identity, a["authority"]),
        ).fetchone()
        if not ref:
            raise Denied("Attention unavailable")
        d = c.execute(
            "SELECT * FROM hb_delivery WHERE id=%s FOR UPDATE", (ref["delivery"],)
        ).fetchone()
        row = c.execute("SELECT * FROM hb_attention WHERE id=%s FOR UPDATE", (identity,)).fetchone()
        receipt(store, c, a, row, d)
        question = row["method"] in ("hollywood/question", "item/tool/requestUserInput")
        if question and (
            a["kind"] != "human" or (a["id"] != row["human"] and not a["administrator"])
        ):
            raise Denied("Attributable human or enterprise human administrator required")
        if not question and a["id"] != row["human"] and not a["administrator"]:
            raise Denied("Attributable human or enterprise administrator required")
        if payload["scope"] != scope(row, d):
            raise Conflict("Reply environment/execution/action scope changed")
        fingerprint = digest(payload)
        old = c.execute(
            "SELECT * FROM hb_attention_command WHERE attention=%s AND principal=%s AND operation=%s",
            (identity, a["id"], operation),
        ).fetchone()
        if old:
            if old["digest"] != fingerprint:
                raise Conflict("Response operation contents changed")
            return old["result"]
        if (
            row["state"] != "pending"
            or not continuations.accepting(c, row, d)
            or d["generation"] != row["generation"]
        ):
            raise Conflict("Native attention is no longer pending")
        response = payload["response"]
        if row["method"] in ("hollywood/question", "item/tool/requestUserInput"):
            expected = {q["id"] for q in row["request"]["questions"]}
            if set(response.get("answers", {})) != expected:
                raise ValueError("Every original question needs an answer")
        elif response.get("decision") not in ("accept", "decline"):
            raise ValueError("This action accepts only accept or decline")
        if continuations.nonblocking(row):
            eid = continuations.enqueue(store, c, a, row, d, response)
        else:
            eid = store.event(
                c, a, row["room"], "control",
                "Response accepted; native submission pending",
                recipient=row["agent"], audience="direct", source=row["event"],
                data={"attentionId": identity, "state": "response_submitted"},
            )
        c.execute(
            "UPDATE hb_attention SET response=%s,response_author=%s,response_event=%s,state='response_submitted',revision=revision+1,updated_at=clock_timestamp() WHERE id=%s",
            (Jsonb(response), a["id"], eid, identity),
        )
        row = c.execute("SELECT * FROM hb_attention WHERE id=%s", (identity,)).fetchone()
        out = receipt(store, c, a, row, d)
        c.execute(
            "INSERT INTO hb_attention_command VALUES(%s,%s,%s,%s,%s)",
            (identity, a["id"], operation, fingerprint, Jsonb(out)),
        )
        return out


def acknowledge(store, actor_id, identity, operation, payload):
    with store.pool.connection() as c:
        a = store.actor(c, actor_id)
        ref = c.execute(
            "SELECT delivery FROM hb_attention WHERE id=%s AND authority=%s",
            (identity, a["authority"]),
        ).fetchone()
        if not ref:
            raise Denied("Attention unavailable")
        d = owned_delivery(
            store, c, a, str(ref["delivery"]), payload["claimId"], payload["generation"]
        )
        row = c.execute("SELECT * FROM hb_attention WHERE id=%s FOR UPDATE", (identity,)).fetchone()
        if row["agent"] != a["id"] or payload["scope"] != scope(row, d):
            raise Conflict("Native acknowledgement owner changed")
        fingerprint = digest(payload)
        old = c.execute(
            "SELECT * FROM hb_attention_command WHERE attention=%s AND principal=%s AND operation=%s",
            (identity, a["id"], operation),
        ).fetchone()
        if old:
            if old["digest"] != fingerprint:
                raise Conflict("Acknowledgement contents changed")
            return old["result"]
        state = payload["state"]
        if payload.get("reason") and (state!="expired" or row["state"]!="pending"
                or not continuations.nonblocking(row)):
            raise Conflict("Only an unanswered nonblocking question may be withdrawn with a reason")
        allowed = {
            "pending": {"expired", "unknown"},
            "response_submitted": {"native_submitted", "expired", "unknown"},
            "native_submitted": {"closed", "unknown"},
            "unknown": {"expired", "closed"},
        }
        if state not in allowed.get(row["state"], set()):
            raise Conflict("Invalid attention transition")
        eid = store.event(
            c,
            a,
            row["room"],
            "control",
            "Native attention " + state,
            recipient=row["human"],
            audience="direct",
            source=row["event"],
            data={"attentionId": identity, "state": state, "nativeConsumptionProved": False,
                  **({"reason":payload["reason"],"withdrawnByAgent":True}
                     if state=="expired" and payload.get("reason") else {})},
        )
        c.execute(
            "UPDATE hb_attention SET state=%s,revision=revision+1,updated_at=clock_timestamp() WHERE id=%s",
            (state, identity),
        )
        row = c.execute("SELECT * FROM hb_attention WHERE id=%s", (identity,)).fetchone()
        out = receipt(store, c, a, row, d)
        out["eventId"] = eid
        c.execute(
            "INSERT INTO hb_attention_command VALUES(%s,%s,%s,%s,%s)",
            (identity, a["id"], operation, fingerprint, Jsonb(out)),
        )
        return out


def command(store, actor_id, identity, operation):
    """Read this actor's original response receipt without replaying its write."""
    with store.pool.connection() as c:
        a = store.actor(c, actor_id)
        row = c.execute(
            "SELECT * FROM hb_attention WHERE id=%s AND authority=%s", (identity, a["authority"])
        ).fetchone()
        if not row:
            raise Denied("Attention unavailable")
        d = c.execute("SELECT * FROM hb_delivery WHERE id=%s", (row["delivery"],)).fetchone()
        receipt(store, c, a, row, d)
        old = c.execute(
            "SELECT result FROM hb_attention_command WHERE attention=%s AND principal=%s AND operation=%s",
            (identity, actor_id, operation),
        ).fetchone()
        if not old:
            raise Denied("Attention command unavailable")
        return old["result"]
