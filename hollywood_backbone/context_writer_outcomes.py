"""Original native Writer outcomes enter the same agent's Reader inbox."""
from uuid import UUID, uuid5
from psycopg.types.json import Jsonb
from .store import Conflict, Denied, digest, json_value
from .context_system import item, change
from .context_peers import require_current_read

SCHEMA = "lax-writer-outcome/v2"
MAX_CONTINUATIONS = 2


def basis(work):
    value = work["value"]
    return digest({"objective": value.get("objective"), "acceptance": value.get("acceptance"),
        "updates": [u for u in value.get("updates", [])
                    if u.get("originKind") != "native_outcome"]})


def observe(store, c, actor, purpose, binding, p):
    from .context_work_frontier import terminal_permit
    v = p["value"]
    if purpose != "writer":
        raise Denied("Only the original Writer runtime observes native outcomes")
    if (set(v) != {"nativeTurn", "status", "effectsReconciled", "nativeItemsSha256", "items", "error", "itemCount", "itemCounts", "projectionComplete", "errorChars", "errorSha256"}
            or v["status"] not in ("failed", "completed")
            or type(v["effectsReconciled"]) is not bool
            or not isinstance(v["nativeItemsSha256"], str)
            or len(v["nativeItemsSha256"]) != 64
            or any(x not in "0123456789abcdef" for x in v["nativeItemsSha256"])
            or not isinstance(v["items"], list) or len(v["items"]) > 8
            or type(v["itemCount"]) is not int or v["itemCount"] < len(v["items"])
            or not isinstance(v["itemCounts"], dict)
            or any(not isinstance(k,str) or type(n) is not int or n<0 for k,n in v["itemCounts"].items())
            or sum(v["itemCounts"].values()) != v["itemCount"]
            or type(v["projectionComplete"]) is not bool
            or v["projectionComplete"] != (v["itemCount"] == len(v["items"]))
            or type(v["errorChars"]) is not int or v["errorChars"] < 0
            or not isinstance(v["errorSha256"],str) or len(v["errorSha256"]) != 64
            or any(x not in "0123456789abcdef" for x in v["errorSha256"])
            or not isinstance(v["error"], str) or len(v["error"]) > 4000
            or p["nativeTurn"] != v["nativeTurn"]):
        raise ValueError("Exact bounded original native outcome required")
    permit = terminal_permit(c, actor, purpose, binding, p, terminal_status=v["status"])
    inputs, unchanged = [], []
    for identity in permit["material_ids"]:
        work = item(c, store, actor, str(identity), "work")
        execution = work["value"].get("execution", {})
        if (work["state"] not in ("pending", "running", "waiting")
                or execution.get("nativeTurn") != v["nativeTurn"]
                or execution.get("nativeThread") != p["nativeThread"]
                or execution.get("status") != v["status"]):
            unchanged.append(str(identity))
            continue
        delivery = c.execute("SELECT * FROM hb_delivery WHERE event=%s AND agent=%s",
                             (work["source"], actor["id"])).fetchone()
        if not delivery or delivery["cancellation_requested"] or work["state"] == "waiting":
            unchanged.append(str(identity))
            continue
        key = uuid5(binding["context_id"], "writer-outcome:" + v["nativeTurn"] + ":" + str(identity))
        outcome = {"schema": SCHEMA, "originKind": "native_outcome",
            "actorId": str(actor["id"]), "contextId": str(binding["context_id"]),
            "nativeThread": p["nativeThread"], "nativeTurn": v["nativeTurn"],
            "workId": str(identity), "permitId": str(permit["id"]),
            "status": v["status"], "effectsReconciled": v["effectsReconciled"],
            "nativeItemsSha256": v["nativeItemsSha256"], "items": v["items"],
            "itemCount":v["itemCount"],"itemCounts":v["itemCounts"],
            "projectionComplete":v["projectionComplete"],
            "errorChars":v["errorChars"],"errorSha256":v["errorSha256"],
            "evidenceRetrieval":"Original native thread/turn retained; authorized Hollywood room activity and original Work evidence remain available. Recent descriptors are not complete execution results.",
            "error": v["error"], "sourceEventId": str(work["source"]),
            "meaning": "Internal native observation, not human input, assignment, room message or proof of Work completion."}
        c.execute("""INSERT INTO hb_context_material(id,agent,kind,source,state,value)
            VALUES(%s,%s,'input',%s,'pending',%s) ON CONFLICT(id) DO NOTHING""",
            (key, actor["id"], work["source"], Jsonb({"writerOutcome": outcome})))
        existing = item(c, store, actor, str(key), "input")
        if existing["value"].get("writerOutcome") != outcome:
            raise Conflict("Original native outcome changed")
        inputs.append(str(key))
    return {"schema": SCHEMA, "nativeThread": p["nativeThread"], "nativeTurn": v["nativeTurn"],
        "reviewInputIds": inputs, "unchangedWorkIds": unchanged,
        "newExecutionAdmitted": False, "originalInputRepeated": False}


def review(c, store, actor, binding, p):
    v = p["value"]
    if (set(v) != {"inputId", "workId", "workRevision", "decision", "body"}
            or v["decision"] not in ("continue", "hold")
            or not isinstance(v["body"], str) or not v["body"].strip() or len(v["body"]) > 4000
            or type(v["workRevision"]) is not int):
        raise ValueError("Current original Work and bounded native-outcome assessment required")
    source = item(c, store, actor, v["inputId"], "input")
    outcome = source["value"].get("writerOutcome", {})
    if source["state"] != "pending" or outcome.get("schema") not in (SCHEMA, "lax-writer-outcome/v1"):
        raise Conflict("Pending original native outcome required")
    work = item(c, store, actor, v["workId"], "work")
    require_current_read(binding, source, p["nativeTurn"])
    require_current_read(binding, work, p["nativeTurn"])
    if outcome.get("workId") != str(work["id"]) or source["source"] != work["source"]:
        raise Denied("Outcome belongs to different original Work")
    if work["revision"] != v["workRevision"]:
        raise Conflict("Original Work changed; reread current state")
    execution = work["value"].get("execution", {})
    current = (execution.get("nativeThread") == outcome["nativeThread"]
               and execution.get("nativeTurn") == outcome["nativeTurn"]
               and execution.get("status") == outcome["status"])
    continued = False
    count = 0
    if v["decision"] == "continue":
        if (not current or work["state"] not in ("pending", "running")
                or outcome["effectsReconciled"] is not True):
            raise Conflict("Changed, waiting, terminal or uncertain Work cannot continue")
        from .context_work_frontier import runnable
        if runnable(work):
            raise Conflict("Original Work already has a continuation")
        event = store.readable_event(c, actor, work["source"])
        store.room(c, actor, event["room"], "can_post")
        from .context_role_work import validate
        validate(c, store, actor, event)
        delivery = c.execute("SELECT * FROM hb_delivery WHERE event=%s AND agent=%s",
                             (work["source"], actor["id"])).fetchone()
        if (not delivery or delivery["cancellation_requested"]
                or delivery["state"] not in ("claimed", "running")):
            raise Conflict("Original execution is cancelled, uncertain or closed")
        if c.execute("""SELECT 1 FROM hb_edit_permit WHERE authority=%s
            AND source->>'deliveryId'=%s AND state<>'settled' LIMIT 1""",
            (actor["authority"], str(delivery["id"]))).fetchone():
            raise Conflict("Original edit effects remain unsettled")
        key = basis(work)
        prior = work["value"].get("nativeOutcomeContinuation", {})
        count = prior.get("count", 0) if prior.get("basisDigest") == key else 0
        if count >= MAX_CONTINUATIONS:
            raise Conflict("Native-outcome continuation budget exhausted; assess and hold honestly")
        entry = {"originKind": "native_outcome", "inputId": str(source["id"]),
            "authorId": str(actor["id"]), "authorKind": "agent", "readerNativeTurn": p["nativeTurn"],
            "writerNativeThread": outcome["nativeThread"], "writerNativeTurn": outcome["nativeTurn"],
            "interpretation": v["body"]}
        change(c, work, "pending", {**work["value"],
            "updates": (work["value"].get("updates", []) + [entry])[-16:],
            "resumeRevision": work["value"].get("resumeRevision", 0) + 1,
            "nativeOutcomeContinuation": {"basisDigest": key, "count": count + 1}})
        continued = True
    change(c, source, "handled", {**source["value"], "review": {
        "decision": v["decision"], "body": v["body"], "readerNativeTurn": p["nativeTurn"],
        "workRevision": work["revision"], "continued": continued}})
    return {"inputId": str(source["id"]), "workId": str(work["id"]), "state": "handled",
        "continued": continued, "objectiveReplaced": False, "writerSteered": False,
        "continuationsRemaining": MAX_CONTINUATIONS - count - int(continued),
        "workRevision": work["revision"] + int(continued), "responseRequired": False}
