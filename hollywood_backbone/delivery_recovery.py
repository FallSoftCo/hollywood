"""Explicit recovery of a failed attempt that never admitted native execution."""
from psycopg.types.json import Jsonb

from .store import Conflict, json_value


def change(store, c, actor, identity, payload):
    d = c.execute(
        "SELECT * FROM hb_delivery WHERE id=%s AND authority=%s FOR UPDATE",
        (identity, actor["authority"]),
    ).fetchone()
    if not d:
        raise Conflict("Original failed delivery unavailable")
    event = store.readable_event(c, actor, d["event"])
    store.room(c, actor, event["room"], "can_request")
    if (d["revision"], d["generation"], str(d["result_event"])) != (
        payload["revision"], payload["generation"], payload["resultEventId"]
    ):
        raise Conflict("Original failure revision changed")
    if (d["state"] != "failed" or d["cancellation_requested"] or
            d["native_thread"] is not None or d["native_turn"] is not None or
            d["result_event"] is None):
        raise Conflict("Only a settled failure before native admission may recover")
    # Every supported native launch durably reserves its executor first. Absence
    # is required across all generations; a stopped executor is still execution.
    tables = ("hb_executor", "hb_agent_execution", "hb_native_goal_binding",
              "hb_native_effect", "hb_activity", "hb_attention", "hb_work_attempt")
    for table in tables:
        if c.execute("SELECT 1 FROM " + table + " WHERE delivery=%s LIMIT 1",
                     (identity,)).fetchone():
            raise Conflict("Original native admission or workflow evidence requires separate reconciliation")
    result = c.execute(
        "SELECT * FROM hb_event WHERE id=%s AND authority=%s",
        (d["result_event"], actor["authority"]),
    ).fetchone()
    if not result or any((
        result["kind"] != "result", result["source"] != d["event"],
        result["author"] != d["agent"], result["data"].get("state") != "failed",
        result["data"].get("generation") != d["generation"],
        result["data"].get("deliveryId") != identity,
    )):
        raise Conflict("Original failed result does not match the attempt")
    c.execute(
        """INSERT INTO hb_delivery_recovery
           (authority,delivery,generation,actor,operation,before_snapshot,note)
           VALUES(%s,%s,%s,%s,%s,%s,%s)""",
        (actor["authority"], identity, d["generation"], actor["id"],
         payload["operationId"], Jsonb(json_value(d)), payload["note"]),
    )
    after = c.execute(
        """UPDATE hb_delivery SET state='pending',claim_id=NULL,result_event=NULL,
           revision=revision+1,updated_at=clock_timestamp() WHERE id=%s RETURNING revision""",
        (identity,),
    ).fetchone()
    return {
        "schema": "hollywood-pre-native-recovery/v1", "deliveryId": identity,
        "previousGeneration": d["generation"], "previousResultEventId": str(d["result_event"]),
        "state": "pending", "revision": after["revision"],
        "originalAttemptPreserved": True, "nativeExecutionStarted": False,
        "effectReplayAdmitted": False,
        "meaning": "Explicit re-admission requested; normal current host, role, context and cancellation checks still apply",
    }
