"""Explicit human guidance for an existing native turn; never an execution request."""

from psycopg.types.json import Jsonb
from .attention import human_owner, owned_delivery
from .store import Conflict, Denied, digest, json_value

CAPABILITY = "native-human-controls/v1"
OPEN = ("pending", "dispatching", "native_admitted", "unknown")


def scope(delivery, event):
    return json_value({
        "authorityId": delivery["authority"], "deliveryId": delivery["id"],
        "agentId": delivery["agent"], "roomId": event["room"],
        "hostId": delivery["host"], "generation": delivery["generation"],
        "nativeThread": delivery["native_thread"], "nativeTurn": delivery["native_turn"],
        "sourceRequestId": event["id"], "audience": event["audience"],
    })


def human_allowed(store, c, actor, event):
    if actor["kind"] != "human":
        raise Denied("Authenticated human guidance required")
    store.readable_event(c, actor, event["id"])
    store.room(c, actor, event["room"], "can_request")
    if not actor["administrator"] and actor["id"] != human_owner(c, event):
        raise Denied("Original causal human or human administrator required")


def capable(c, delivery):
    return bool(c.execute(
        """SELECT 1 FROM hb_connector_observation WHERE agent=%s AND host=%s
           AND capabilities @> %s
           AND observed_at > clock_timestamp()-interval '180 seconds'""",
        (delivery["agent"], delivery["host"], Jsonb([CAPABILITY])),
    ).fetchone())


def receipt(store, c, actor, row, delivery):
    event = store.readable_event(c, actor, delivery["event"])
    if actor["kind"] == "service":
        raise Denied("Human or native participant required")
    return {
        "control": json_value(row), "scope": scope(delivery, event),
        "deliveryState": delivery["state"],
        "nativeAdmissionProved": row["state"] in ("native_admitted", "native_recorded"),
        "nativeMessageRecorded": row["state"] == "native_recorded",
        "modelConsumptionProved": False,
    }


def create(store, actor_id, identity, operation, payload):
    with store.pool.connection() as c:
        actor = store.actor(c, actor_id)
        delivery = c.execute(
            "SELECT * FROM hb_delivery WHERE id=%s AND authority=%s FOR UPDATE",
            (payload["scope"]["deliveryId"], actor["authority"]),
        ).fetchone()
        if not delivery:
            raise Denied("Execution unavailable")
        event = store.readable_event(c, actor, delivery["event"])
        human_allowed(store, c, actor, event)
        if payload["scope"] != scope(delivery, event):
            raise Conflict("Captured native control scope changed")
        fingerprint = digest(payload)
        old = c.execute("SELECT * FROM hb_native_control WHERE id=%s", (identity,)).fetchone()
        operation_row = c.execute(
            "SELECT * FROM hb_native_control WHERE human=%s AND operation=%s",
            (actor["id"], operation),
        ).fetchone()
        if old or operation_row:
            row = old or operation_row
            if str(row["id"]) != identity or row["human"] != actor["id"] or row["operation"] != operation or row["digest"] != fingerprint:
                raise Conflict("Native guidance identity or contents changed")
            return receipt(store, c, actor, row, delivery)
        if delivery["state"] != "running" or delivery["cancellation_requested"] or not delivery["native_turn"] or not capable(c, delivery):
            raise Conflict("Execution does not currently support active human guidance")
        count = c.execute(
            "SELECT count(*) AS n FROM hb_native_control WHERE delivery=%s AND state=ANY(%s)",
            (delivery["id"], list(OPEN)),
        ).fetchone()["n"]
        if count >= 16:
            raise Conflict("Resolve pending guidance before sending more")
        eid = store.event(
            c, actor, event["room"], "control", payload["body"],
            recipient=delivery["agent"], audience=event["audience"], source=event["id"],
            data={"nativeControlId": identity, "deliveryId": str(delivery["id"]), "state": "pending", "action": "steer"},
        )
        c.execute(
            """INSERT INTO hb_native_control
               (id,authority,delivery,agent,human,room,event,generation,claim_id,
                native_thread,native_turn,operation,body,digest,captured_scope)
               VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (identity, actor["authority"], delivery["id"], delivery["agent"], actor["id"],
             event["room"], eid, delivery["generation"], delivery["claim_id"],
             delivery["native_thread"], delivery["native_turn"], operation,
             payload["body"], fingerprint, Jsonb(payload["scope"])),
        )
        row = c.execute("SELECT * FROM hb_native_control WHERE id=%s", (identity,)).fetchone()
        return receipt(store, c, actor, row, delivery)


def read(store, actor_id, identity):
    with store.pool.connection() as c:
        actor = store.actor(c, actor_id)
        row = c.execute(
            "SELECT * FROM hb_native_control WHERE id=%s AND authority=%s",
            (identity, actor["authority"]),
        ).fetchone()
        if not row:
            raise Denied("Native guidance unavailable")
        delivery = c.execute("SELECT * FROM hb_delivery WHERE id=%s", (row["delivery"],)).fetchone()
        return receipt(store, c, actor, row, delivery)


def pending(store, actor_id, identity, payload):
    with store.pool.connection() as c:
        actor = store.actor(c, actor_id)
        delivery = owned_delivery(store, c, actor, identity, payload["claimId"], payload["generation"])
        if str(delivery["host"]) != payload["hostId"]:
            raise Conflict("Native host changed")
        # This authenticated protocol observation never admits a model turn.
        c.execute(
            """UPDATE hb_connector_observation SET observed_at=clock_timestamp()
               WHERE agent=%s AND host=%s AND capabilities @> %s""",
            (actor["id"], delivery["host"], Jsonb([CAPABILITY])),
        )
        rows = c.execute(
            """SELECT n.* FROM hb_native_control n JOIN hb_event e ON e.id=n.event
               WHERE n.delivery=%s AND n.state=ANY(%s) ORDER BY e.seq LIMIT 16""",
            (identity, list(OPEN)),
        ).fetchall()
        return {"controls": [receipt(store, c, actor, row, delivery) for row in rows]}


def update(store, actor_id, identity, operation, payload):
    with store.pool.connection() as c:
        actor = store.actor(c, actor_id)
        ref = c.execute(
            "SELECT delivery FROM hb_native_control WHERE id=%s AND authority=%s",
            (identity, actor["authority"]),
        ).fetchone()
        if not ref:
            raise Denied("Native guidance unavailable")
        delivery = owned_delivery(store, c, actor, str(ref["delivery"]), payload["claimId"], payload["generation"])
        row = c.execute("SELECT * FROM hb_native_control WHERE id=%s FOR UPDATE", (identity,)).fetchone()
        if payload["scope"] != row["captured_scope"] or payload["scope"] != scope(
            delivery, store.readable_event(c, actor, delivery["event"])
        ):
            raise Conflict("Native guidance owner changed")
        fingerprint = digest(payload)
        old = c.execute(
            "SELECT * FROM hb_native_control_command WHERE control=%s AND operation=%s",
            (identity, operation),
        ).fetchone()
        if old:
            if old["digest"] != fingerprint:
                raise Conflict("Native guidance acknowledgement changed")
            return old["result"]
        state = payload["state"]
        allowed = {
            "pending": {"dispatching", "rejected"},
            "dispatching": {"native_admitted", "native_recorded", "unknown", "rejected"},
            "native_admitted": {"native_recorded", "unknown"},
            "unknown": {"native_recorded"},
        }
        if state not in allowed.get(row["state"], set()):
            raise Conflict("Invalid native guidance transition")
        if state == "dispatching":
            original = c.execute("SELECT * FROM hb_event WHERE id=%s", (delivery["event"],)).fetchone()
            try:
                human_allowed(store, c, store.actor(c, row["human"]), original)
                if delivery["state"] != "running" or delivery["cancellation_requested"] or not capable(c, delivery):
                    raise Conflict("Original execution unavailable")
            except (Denied, Conflict):
                state = "rejected"
                payload = {**payload, "evidence": {"reason": "authorization-or-execution-changed"}}
        if state == "rejected" and row["state"] != "pending" and payload["evidence"].get("reason") != "native-not-sent":
            raise Conflict("Possible native submission must remain unknown")
        if state in ("native_admitted", "native_recorded"):
            if payload["evidence"].get("nativeTurn") != row["native_turn"]:
                raise Conflict("Original native turn receipt required")
            if state == "native_recorded" and payload["evidence"].get("clientUserMessageId") != "hollywood-control-" + identity:
                raise Conflict("Original native message identity required")
        eid = store.event(
            c, actor, row["room"], "control", "Human guidance " + state,
            recipient=row["human"], audience="direct", source=row["event"],
            data={"nativeControlId": identity, "state": state, "modelConsumptionProved": False},
        )
        c.execute(
            """UPDATE hb_native_control SET state=%s,evidence=%s,revision=revision+1,
               updated_at=clock_timestamp() WHERE id=%s""",
            (state, Jsonb(payload["evidence"]), identity),
        )
        row = c.execute("SELECT * FROM hb_native_control WHERE id=%s", (identity,)).fetchone()
        result = {**receipt(store, c, actor, row, delivery), "eventId": eid}
        c.execute(
            "INSERT INTO hb_native_control_command VALUES(%s,%s,%s,%s)",
            (identity, operation, fingerprint, Jsonb(result)),
        )
        return result


def terminal(store, c, actor, delivery):
    """Close only undispatched input; possible native admission stays explicitly unknown."""
    rows = c.execute(
        "SELECT * FROM hb_native_control WHERE delivery=%s AND state IN ('pending','dispatching') FOR UPDATE",
        (delivery["id"],),
    ).fetchall()
    for row in rows:
        state = "rejected" if row["state"] == "pending" else "unknown"
        c.execute(
            """UPDATE hb_native_control SET state=%s,revision=revision+1,
               evidence=%s,updated_at=clock_timestamp() WHERE id=%s""",
            (state, Jsonb({"reason": "original-native-execution-terminal"}), row["id"]),
        )
        store.event(c, actor, row["room"], "control", "Human guidance " + state,
                    recipient=row["human"], audience="direct", source=row["event"],
                    data={"nativeControlId": str(row["id"]), "state": state, "modelConsumptionProved": False})
