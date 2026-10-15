"""Independent executor-resource facts; stopped never means unknown effects were undone."""
from psycopg.types.json import Jsonb
from .store import Conflict, Denied, digest, json_value

ACTIVE = ("captured", "live", "unknown")

def controller(store, c, actor, host):
    policy = c.execute("SELECT * FROM hb_host_admission WHERE host=%s AND authority=%s",
                       (host, actor["authority"])).fetchone()
    if not policy or actor["kind"] != "service" or policy["controller"] != actor["id"]:
        raise Denied("Enrolled host controller required")
    return policy

def value(row, delivery):
    return json_value({"schema": "hollywood-executor/v1", "executor": row,
                       "deliveryState": delivery["state"],
                       "deliveryTerminal": delivery["state"] in ("completed", "failed", "cancelled"),
                       "currentDeliveryGeneration": delivery["generation"],
                       "effectOutcomeNotInferredFromExecutor": True,
                       "stopDoesNotUndoExternalEffects": True})

def command(c, actor, operation, fingerprint):
    c.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
              ("executor:" + str(actor["id"]) + ":" + operation,))
    old = c.execute("SELECT * FROM hb_executor_command WHERE principal=%s AND operation=%s",
                    (actor["id"], operation)).fetchone()
    if old and old["digest"] != fingerprint:
        raise Conflict("Executor operation changed")
    return old["result"] if old else None

def finish(c, actor, operation, fingerprint, result):
    c.execute("INSERT INTO hb_executor_command VALUES(%s,%s,%s,%s)",
              (actor["id"], operation, fingerprint, Jsonb(result)))
    return result

def begin(store, actor_id, identity, payload):
    with store.pool.connection() as c:
        actor = store.actor(c, actor_id)
        d = c.execute("SELECT * FROM hb_delivery WHERE id=%s AND agent=%s FOR UPDATE",
                      (identity, actor["id"])).fetchone()
        if not d:
            raise Denied("Executor delivery unavailable")
        if (d["generation"], d["claim_id"], str(d["host"])) != (
            payload["generation"], payload["claimId"], payload["hostId"]
        ):
            raise Conflict("Executor claim owner changed")
        if not c.execute("SELECT 1 FROM hb_placement WHERE agent=%s AND host=%s AND enabled",
                         (actor["id"], d["host"])).fetchone():
            raise Denied("Executor placement disabled")
        operation = payload["operationId"]
        fingerprint = digest({"deliveryId": identity, **payload})
        old = command(c, actor, operation, fingerprint)
        if old:
            return old
        if d["state"] != "claimed":
            raise Conflict("Only an original claimed delivery may capture an executor")
        policy = c.execute("SELECT * FROM hb_host_admission WHERE host=%s", (d["host"],)).fetchone()
        if not policy or policy["machine_identity"] != payload["machineIdentity"]:
            raise Conflict("Enrolled executor host changed")
        if payload["guardianPid"] == payload["nativePid"]:
            raise ValueError("Guardian and native process must be distinct")
        if ".." in payload["controlGroup"].split("/") or not payload["controlGroup"].startswith("/"):
            raise ValueError("Invalid executor control group")
        descriptor = {k: v for k, v in payload.items()
                      if k not in ("operationId", "id", "claimId", "generation", "hostId")}
        c.execute(
            """INSERT INTO hb_executor
               (id,authority,delivery,agent,host,generation,claim_id,descriptor,descriptor_digest)
               VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (payload["id"], actor["authority"], identity, actor["id"], d["host"],
             d["generation"], d["claim_id"], Jsonb(descriptor), digest(descriptor)),
        )
        row = c.execute("SELECT * FROM hb_executor WHERE id=%s", (payload["id"],)).fetchone()
        return finish(c, actor, operation, fingerprint, value(row, d))

def read(store, actor_id, identity):
    with store.pool.connection() as c:
        actor = store.actor(c, actor_id)
        row = c.execute("SELECT * FROM hb_executor WHERE id=%s AND authority=%s",
                        (identity, actor["authority"])).fetchone()
        if not row:
            raise Denied("Executor unavailable")
        if actor["id"] != row["agent"]:
            controller(store, c, actor, row["host"])
        d = c.execute("SELECT * FROM hb_delivery WHERE id=%s", (row["delivery"],)).fetchone()
        if actor["id"] == row["agent"]:
            store.readable_event(c, actor, d["event"])
        return value(row, d)

def listing(store, actor_id, host, after=None, limit=256):
    with store.pool.connection() as c:
        actor = store.actor(c, actor_id)
        controller(store, c, actor, host)
        rows = c.execute(
            """SELECT x.* FROM hb_executor x
               WHERE x.host=%s AND x.authority=%s AND x.state<>'stopped'
               AND (%s::uuid IS NULL OR x.id>%s::uuid)
               ORDER BY x.id LIMIT %s""", (host, actor["authority"], after, after, limit)
        ).fetchall()
        return json_value({"executors": rows, "nextAfter": rows[-1]["id"] if len(rows) == limit else None})

def observe(store, actor_id, host, identity, payload):
    with store.pool.connection() as c:
        actor = store.actor(c, actor_id)
        policy = controller(store, c, actor, host)
        from .admission import lock
        lock(c, host)
        row = c.execute("SELECT * FROM hb_executor WHERE id=%s AND host=%s AND authority=%s FOR UPDATE",
                        (identity, host, actor["authority"])).fetchone()
        if not row:
            raise Denied("Executor unavailable")
        if payload["machineIdentity"] != policy["machine_identity"]:
            raise Conflict("Controller machine identity changed")
        if payload["descriptorDigest"] != row["descriptor_digest"]:
            raise Conflict("Original executor descriptor changed")
        fingerprint = digest({"executorId": identity, "hostId": host, **payload})
        old = command(c, actor, payload["operationId"], fingerprint)
        if old:
            return old
        if row["state"] == "stopped":
            raise Conflict("Stopped executor observations are immutable")
        evidence = payload["evidence"]
        reason = evidence.get("reason")
        if payload["state"] == "live" and reason != "matched-live-incarnation":
            raise ValueError("Positive executor-incarnation evidence required")
        if payload["state"] == "stopped" and reason not in (
            "registered-unit-drained", "registered-invocation-settled", "machine-boot-changed"
        ):
            raise ValueError("Conclusive executor stop evidence required")
        if evidence.get("originalBootId") != row["descriptor"]["bootId"]:
            raise Conflict("Observation refers to another original boot")
        for key in ("serviceUnit", "serviceInvocation", "guardianPid", "guardianStart", "nativePid", "nativeStart"):
            evidence_key = "originalServiceInvocation" if key == "serviceInvocation" else key
            if evidence.get(evidence_key) != row["descriptor"][key]:
                raise Conflict("Observation refers to another original executor")
        binding_digest = evidence.get("serviceBindingDigest", "")
        if payload["state"] in ("live", "stopped") and (
            len(binding_digest) != 64 or any(v not in "0123456789abcdef" for v in binding_digest)
        ):
            raise ValueError("Protected service binding evidence required")
        if payload["state"] == "stopped":
            if reason == "machine-boot-changed" and evidence.get("observedBootId") == evidence["originalBootId"]:
                raise ValueError("Boot change evidence conflicts")
            if reason == "registered-unit-drained" and (
                evidence.get("remainingWorkPids") != [] or evidence.get("remainingWorkProcessCount") != 0
            ):
                raise ValueError("A drained executor group must have no remaining work process")
        d = c.execute("SELECT * FROM hb_delivery WHERE id=%s FOR UPDATE", (row["delivery"],)).fetchone()
        c.execute(
            """UPDATE hb_executor SET state=%s,controller=%s,evidence=%s,
               revision=revision+1,observed_at=clock_timestamp() WHERE id=%s""",
            (payload["state"], actor["id"], Jsonb(evidence), identity),
        )
        # Physical stop and unresolved semantic effects remain separate facts.
        if payload["state"] == "stopped":
            current = d["generation"] == row["generation"] and d["claim_id"] == row["claim_id"]
            if current and d["state"] in ("claimed", "running", "cancel_requested"):
                c.execute("UPDATE hb_delivery SET state='unknown',revision=revision+1,updated_at=clock_timestamp() WHERE id=%s",
                          (d["id"],))
                c.execute("""UPDATE hb_attention SET state=CASE WHEN state='pending' THEN 'expired' ELSE 'unknown' END,
                             revision=revision+1,updated_at=clock_timestamp() WHERE delivery=%s
                             AND generation=%s AND state IN ('pending','response_submitted','native_submitted')""",
                          (d["id"], row["generation"]))
                d = {**d, "state": "unknown"}
            event = c.execute("SELECT * FROM hb_event WHERE id=%s", (d["event"],)).fetchone()
            store.event(c, actor, event["room"], "progress",
                        "The original executor is stopped. Unresolved effects remain held; no work was replayed.",
                        audience=event["audience"], recipient=event["author"] if event["audience"] == "direct" else None,
                        source=event["id"], data={"type": "executorState", "executorId": identity,
                        "deliveryId": str(d["id"]), "generation": row["generation"],
                        "resourceState": "stopped", "deliveryState": d["state"],
                        "externalEffectsUndone": False})
        row = c.execute("SELECT * FROM hb_executor WHERE id=%s", (identity,)).fetchone()
        return finish(c, actor, payload["operationId"], fingerprint, value(row, d))
