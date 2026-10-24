"""Authorize original same-agent native evidence without executing or reading host files."""
from .store import Conflict, Denied, json_value
from .context_system import item, change


def scope(c, store, actor, source):
    outcome = source["value"].get("writerOutcome", {})
    if outcome.get("schema") not in ("lax-writer-outcome/v1", "lax-writer-outcome/v2"):
        raise Denied("Original native Writer outcome required")
    writer = c.execute("SELECT * FROM hb_purpose_context WHERE agent=%s AND purpose='writer'",
                       (actor["id"],)).fetchone()
    if (not writer or outcome["actorId"] != str(actor["id"])
            or outcome["contextId"] != str(writer["context_id"])
            or outcome["nativeThread"] != writer["native_thread"]):
        raise Denied("Evidence belongs to another native Writer")
    begin = c.execute("""SELECT result FROM hb_context_operation
        WHERE agent=%s AND purpose='writer' AND operation=%s""",
        (actor["id"], "context-begin:" + outcome["nativeTurn"])).fetchone()
    scopes = begin["result"].get("executionScopes", []) if begin else []
    matching = [s for s in scopes if s["materialId"] == outcome["workId"]
        and s["agentId"] == str(actor["id"]) and s["nativeThread"] == outcome["nativeThread"]
        and s["nativeTurn"] == outcome["nativeTurn"]
        and s["sourceRequestId"] == str(source["source"])]
    if len(matching) != 1:
        raise Conflict("Original native execution evidence scope unavailable")
    ended = c.execute("""SELECT result FROM hb_context_operation
        WHERE agent=%s AND purpose='writer' AND operation=%s""",
        (actor["id"], "context-end:" + outcome["nativeTurn"])).fetchone()
    permit = c.execute("SELECT * FROM hb_context_permit WHERE id=%s AND agent=%s AND purpose='writer'",
                       (outcome["permitId"], actor["id"])).fetchone()
    if (not ended or ended["result"].get("nativeTurn") != outcome["nativeTurn"]
            or ended["result"].get("state") != outcome["status"]
            or not (ended["result"].get("admission") or {}).get("capacityReleased")
            or (ended["result"].get("admission") or {}).get("permitId") != outcome["permitId"]
            or not permit or permit["state"] != "released"
            or permit["native_thread"] != outcome["nativeThread"]
            or permit["native_turn"] != outcome["nativeTurn"]
            or str(permit["context_id"]) != outcome["contextId"]
            or str(permit["host"]) != matching[0]["hostId"]
            or matching[0]["authorityId"] != str(actor["authority"])):
        raise Conflict("Original native inference release unavailable")
    owner = matching[0]
    store.readable_event(c, actor, source["source"])
    return {"schema": "lax-reader-native-evidence/v1", "inputId": str(source["id"]),
        **{k: owner[k] for k in ("authorityId", "agentId", "hostId", "contextId",
            "nativeThread", "nativeTurn", "sourceRequestId", "materialId")},
        "nativeItemsSha256": outcome["nativeItemsSha256"],
        "itemCount": outcome.get("itemCount"), "readOnly": True}


def authorize(c, store, actor, purpose, binding, p):
    if purpose != "reader" or set(p["value"]) != {"inputId"}:
        raise Denied("Original native evidence reading belongs to the Reader")
    return scope(c, store, actor, item(c, store, actor, p["value"]["inputId"], "input"))


def available(c, store, actor, purpose, binding, p):
    from .context_work_frontier import runnable
    v = p["value"]
    if (purpose != "writer" or set(v) != {"nativeTurn", "nativeItemsSha256"}
            or p["nativeTurn"] != v["nativeTurn"] or binding["active_turn"] is not None):
        raise Denied("Original idle Writer evidence receipt required")
    # Same deterministic identity as observe_writer_outcome, not a new input.
    permit = c.execute("""SELECT result FROM hb_context_operation
        WHERE agent=%s AND purpose='writer' AND operation=%s""",
        (actor["id"], "writer-outcome:" + v["nativeTurn"])).fetchone()
    ids = permit["result"].get("reviewInputIds", []) if permit else []
    if len(ids) != 1:
        raise Conflict("One original accepted Writer outcome required")
    source = item(c, store, actor, ids[0], "input")
    evidence = scope(c, store, actor, source)
    if (evidence["nativeItemsSha256"] != v["nativeItemsSha256"]
            or evidence["nativeThread"] != p["nativeThread"]):
        raise Conflict("Original native evidence digest changed")
    old = source["value"].get("evidenceAvailable")
    if old:
        if old != evidence:
            raise Conflict("Original native evidence availability changed")
        return {"inputId": str(source["id"]), "evidenceAvailable": True, "requeued": False}
    work = item(c, store, actor, evidence["materialId"], "work")
    execution = work["value"].get("execution", {})
    delivery = c.execute("SELECT * FROM hb_delivery WHERE event=%s AND agent=%s",
                         (source["source"], actor["id"])).fetchone()
    current = (work["state"] in ("pending", "running") and not runnable(work)
        and execution.get("nativeTurn") == evidence["nativeTurn"]
        and execution.get("status") in ("failed", "completed")
        and delivery and not delivery["cancellation_requested"]
        and delivery["state"] in ("claimed", "running"))
    requeue = bool(current and source["state"] == "handled"
                   and source["value"].get("review", {}).get("decision") == "hold")
    value = {**source["value"], "evidenceAvailable": evidence}
    if requeue and value.get("review"):
        value["priorReviews"] = value.get("priorReviews", []) + [value["review"]]
    change(c, source, "pending" if requeue else source["state"], value)
    return {"inputId": str(source["id"]), "evidenceAvailable": True,
            "requeued": requeue, "workContinuationChanged": False,
            "humanInputRepeated": False}
