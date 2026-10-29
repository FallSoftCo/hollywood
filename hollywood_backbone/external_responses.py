"""Terminal replies from explicitly enrolled, unhosted external agent transports.

An existing causal answer settles communication before any native admission.
It supplies no native execution evidence and cannot settle assigned Work.
"""
from psycopg.types.json import Jsonb

from .store import Conflict, Denied, digest, json_value
from . import native_effects

SCHEMA = "hollywood-external-agent-response/v1"


def transport(c, actor, action):
    current = native_effects._request.get()
    from .agent_context import communication_allowed
    if (actor["kind"] != "agent" or not current or not current["entered"]
            or current["actor"] != str(actor["id"]) or current["action"] != action
            or current["source"] is not None
            or not communication_allowed(c, current, actor)):
        raise Denied("Explicit external agent communication transport required")


def original(store, c, actor, identity):
    delivery = c.execute(
        "SELECT * FROM hb_delivery WHERE id=%s AND authority=%s FOR UPDATE",
        (identity, actor["authority"])).fetchone()
    if not delivery or delivery["agent"] != actor["id"]:
        raise Denied("Only the original addressed external agent may answer")
    request = store.readable_event(c, actor, delivery["event"])
    store.room(c, actor, request["room"])
    return delivery, request


def complete(store, actor_id, identity, payload):
    with store.pool.connection() as c:
        actor = store.actor(c, actor_id)
        transport(c, actor, "external_response")
        operation = payload["operationId"]
        fingerprint = digest({"schema": SCHEMA, "deliveryId": identity, **payload})
        c.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                  (str(actor["id"]) + ":" + operation,))
        delivery, request = original(store, c, actor, identity)
        store.room(c, actor, request["room"], "can_post")
        old = c.execute("SELECT * FROM hb_command WHERE principal=%s AND operation=%s",
                        (actor["id"], operation)).fetchone()
        if old:
            if old["digest"] != fingerprint:
                raise Conflict("External response operation reused with changed contents")
            return old["accepted_result"]
        if (delivery["state"] != "pending" or delivery["generation"] != 0
                or any(delivery[key] is not None for key in
                       ("host", "claim_id", "native_thread", "native_turn", "result_event"))
                or delivery["cancellation_requested"]
                or request["kind"] != "request" or not request["data"].get("expectsReply")):
            raise Conflict("Only an original unadmitted reply obligation may be settled")
        if any(c.execute(sql, (identity,)).fetchone() for sql in (
            "SELECT 1 FROM hb_executor WHERE delivery=%s",
            "SELECT 1 FROM hb_work WHERE delivery=%s",
            "SELECT 1 FROM hb_work_attempt WHERE delivery=%s",
        )) or request["data"].get("workId"):
            raise Denied("Native execution and assigned Work require their original completion path")
        answer = store.readable_event(c, actor, payload["responseEventId"])
        if (answer["author"] != actor["id"] or answer["recipient"] != request["author"]
                or answer["room"] != request["room"] or answer["source"] != request["id"]
                or answer["audience"] != request["audience"] or answer["seq"] <= request["seq"]
                or answer["kind"] not in ("message", "request")
                or answer["data"].get("expectsReply")):
            raise Denied("Exact existing attributable causal answer required")
        delivery = c.execute(
            """UPDATE hb_delivery SET state='completed',result_event=%s,revision=revision+1,
               updated_at=clock_timestamp() WHERE id=%s RETURNING *""",
            (answer["id"], identity)).fetchone()
        from .communication import completion
        notification = completion(store, c, actor, request, delivery, answer["id"], "completed",
                                  result_kind="external_agent_response")
        result = json_value({
            "schema": SCHEMA, "deliveryId": identity, "requestEventId": request["id"],
            "responseEventId": answer["id"], "communicationState": "answered",
            "deliveryState": "completed", "resultKind": "external_agent_response",
            "executionState": "not_attested", "nativeExecutionAdmitted": False,
            "workAcceptanceInferred": False, "completionDeliveryId": None,
            "completionEventId": notification, "completionNoticeVersion": 2,
        })
        wire = {"method": "POST", "path": "deliveries/" + identity + "/external-response",
                "body": payload}
        c.execute(
            """INSERT INTO hb_command(authority,principal,operation,digest,event,
               wire_request,accepted_result) VALUES(%s,%s,%s,%s,%s,%s,%s)""",
            (actor["authority"], actor["id"], operation, fingerprint, answer["id"],
             Jsonb(wire), Jsonb(result)))
        return result


def read(store, actor_id, identity, operation):
    with store.pool.connection() as c:
        actor = store.actor(c, actor_id)
        transport(c, actor, "external_response_read")
        c.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                  (str(actor["id"]) + ":" + operation,))
        original(store, c, actor, identity)
        row = c.execute("SELECT * FROM hb_command WHERE principal=%s AND operation=%s",
                        (actor["id"], operation)).fetchone()
        if not row:
            raise ResponseNotFound("External response has no committed receipt")
        value = row["accepted_result"]
        if value.get("schema") != SCHEMA or value.get("deliveryId") != identity:
            raise Conflict("Original external response receipt belongs to another operation")
        return value


class ResponseNotFound(LookupError):
    pass

