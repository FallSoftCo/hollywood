"""Explicit recovery of a failed attempt that never admitted native execution."""
from psycopg.types.json import Jsonb

from .store import Conflict, json_value

def reserved_launch(c, actor, d, payload):
    """Explicit owner attestation, not an inference from a stopped executor."""
    checksum=payload.get("reconciliationSha256")
    if (payload.get("outcome") != "native_launch_not_dispatched"
            or not isinstance(checksum,str) or len(checksum)!=64
            or any(ch not in "0123456789abcdef" for ch in checksum)):
        raise ValueError("Explicit original launch proof digest and outcome required")
    executor = c.execute("SELECT * FROM hb_executor WHERE id=%s AND authority=%s FOR SHARE",
                         (payload["executorId"], actor["authority"])).fetchone()
    if (not executor or executor["delivery"] != d["id"] or executor["agent"] != d["agent"]
            or executor["host"] != d["host"] or executor["generation"] != d["generation"]
            or executor["claim_id"] != d["claim_id"] or executor["state"] != "stopped"
            or executor["launch_phase"] != "reserved" or not executor["controller"]
            or not executor["observed_at"] or executor["descriptor"].get("nativePid") is not None
            or executor["descriptor"].get("nativeStart") is not None
            or executor["evidence"].get("reason") != "registered-invocation-settled"):
        raise Conflict("Exact independently stopped reserved launch required")
    policy = c.execute("SELECT controller FROM hb_host_admission WHERE host=%s AND authority=%s FOR SHARE",
                        (d["host"], actor["authority"])).fetchone()
    if not policy or executor["controller"] != policy["controller"]:
        raise Conflict("Original executor observation is not from the current host controller")
    if c.execute("SELECT 1 FROM hb_executor WHERE delivery=%s AND id<>%s LIMIT 1",
                 (d["id"], executor["id"])).fetchone():
        raise Conflict("Other launch generations require separate reconciliation")
    return {"outcome":payload["outcome"],"reconciliationSha256":payload["reconciliationSha256"],
            "executor":json_value(executor),
            "semantics":"Authenticated administrator attests original durable launch journals show no native dispatch; stopped alone is not proof."}



def change(store, c, actor, identity, payload):
    d = c.execute(
        "SELECT * FROM hb_delivery WHERE id=%s AND authority=%s FOR UPDATE",
        (identity, actor["authority"]),
    ).fetchone()
    if not d:
        raise Conflict("Original failed delivery unavailable")
    event = store.readable_event(c, actor, d["event"])
    store.room(c, actor, event["room"], "can_request")
    if (d["revision"], d["generation"], str(d["result_event"]) if d["result_event"] is not None else None) != (
        payload["revision"], payload["generation"], payload["resultEventId"]
    ):
        raise Conflict("Original failure revision changed")
    reconciled = payload.get("executorId") is not None
    if (d["cancellation_requested"] or d["native_thread"] is not None or d["native_turn"] is not None
            or (not reconciled and (d["state"] != "failed" or d["result_event"] is None))
            or (reconciled and (d["state"] != "unknown" or d["result_event"] is not None))):
        raise Conflict("Only original pre-native failure or explicitly reconciled reserved launch may recover")
    proof = reserved_launch(c, actor, d, payload) if reconciled else None
    tables = ("hb_agent_execution", "hb_native_goal_binding", "hb_native_effect",
              "hb_activity", "hb_attention", "hb_work_attempt", "hb_native_control")
    if not reconciled:
        tables = ("hb_executor",) + tables
    for table in tables:
        if c.execute("SELECT 1 FROM " + table + " WHERE delivery=%s LIMIT 1", (identity,)).fetchone():
            raise Conflict("Original native admission or workflow evidence requires separate reconciliation")
    if not reconciled:
        result = c.execute("SELECT * FROM hb_event WHERE id=%s AND authority=%s",
                           (d["result_event"], actor["authority"])).fetchone()
        if not result or any((result["kind"] != "result", result["source"] != d["event"],
                result["author"] != d["agent"], result["data"].get("state") != "failed",
                result["data"].get("generation") != d["generation"],
                result["data"].get("deliveryId") != identity)):
            raise Conflict("Original failed result does not match the attempt")
    c.execute(
        """INSERT INTO hb_delivery_recovery
           (authority,delivery,generation,actor,operation,before_snapshot,note)
           VALUES(%s,%s,%s,%s,%s,%s,%s)""",
        (actor["authority"], identity, d["generation"], actor["id"],
         payload["operationId"], Jsonb({**json_value(d), **({"preNativeReconciliation":proof} if proof else {})}), payload["note"]),
    )
    after = c.execute(
        """UPDATE hb_delivery SET state='pending',claim_id=NULL,result_event=NULL,
           revision=revision+1,updated_at=clock_timestamp() WHERE id=%s RETURNING revision""",
        (identity,),
    ).fetchone()
    return {
        "schema": "hollywood-pre-native-recovery/v1", "deliveryId": identity,
        "previousGeneration": d["generation"], "previousResultEventId": str(d["result_event"]) if d["result_event"] else None,
        "state": "pending", "revision": after["revision"],
        "originalAttemptPreserved": True, "nativeExecutionStarted": False,
        "effectReplayAdmitted": False,
        "explicitReservedLaunchReconciliation": reconciled,
        "meaning": "Explicit re-admission requested; normal current host, role, context and cancellation checks still apply",
    }
