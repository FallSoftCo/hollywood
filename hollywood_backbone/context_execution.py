"""Original per-turn execution ownership, derived from accepted context receipts.

This module grants no business-effect or process-liveness authority. It provides
captured ownership for shared native logging and the following core integration.
There is no second execution/work acceptance ledger.
"""
from .store import Conflict, Denied, digest, json_value
from .activity_models import normalize


def capture(c, store, actor, purpose, binding, p, material_ids):
    if purpose != "writer":
        return []
    system = c.execute("SELECT * FROM hb_context_system WHERE agent=%s", (actor["id"],)).fetchone()
    scopes = []
    for identity in material_ids:
        work = c.execute("SELECT * FROM hb_context_material WHERE id=%s AND agent=%s AND kind='work'",
                         (identity, actor["id"])).fetchone()
        if not work:
            raise Denied("Original work unavailable")
        d = c.execute("SELECT * FROM hb_delivery WHERE event=%s AND agent=%s FOR UPDATE",
                      (work["source"], actor["id"])).fetchone()
        if (not d or d["state"] != "running" or d["cancellation_requested"]
                or d["generation"] < 1 or not d["claim_id"] or d["host"] != system["host"]):
            raise Conflict("Original delivery no longer owns this execution")
        request = store.readable_event(c, actor, d["event"])
        store.room(c, actor, request["room"], "can_post")
        value = json_value({
            "schema": "lax-execution-scope/v1", "authorityId": actor["authority"],
            "agentId": actor["id"], "hostId": d["host"], "workspaceId": system["workspace"],
            "contextId": binding["context_id"], "purpose": purpose,
            "roleRevision": system["role_revision"], "nativeThread": p["nativeThread"],
            "nativeTurn": p["nativeTurn"], "materialId": work["id"],
            "sourceRequestId": d["event"], "deliveryId": d["id"],
            "claimId": d["claim_id"], "generation": d["generation"],
        })
        scopes.append({"id": "execution-" + digest(value), **value})
    return scopes


def original_scope(c, actor, purpose, binding, p, scope_id):
    if purpose != "writer":
        raise Denied("Only the writer emits public execution activity")
    begin = c.execute("""SELECT result FROM hb_context_operation
        WHERE agent=%s AND purpose=%s AND operation=%s""",
        (actor["id"], purpose, "context-begin:" + p["nativeTurn"])).fetchone()
    scopes = begin["result"].get("executionScopes", []) if begin else []
    matched = [s for s in scopes if s["id"] == scope_id]
    if len(matched) != 1:
        raise Denied("Accepted original execution scope unavailable")
    scope = matched[0]
    expected = {"agentId": str(actor["id"]), "authorityId": str(actor["authority"]),
                "purpose": purpose, "contextId": str(binding["context_id"]),
                "nativeThread": p["nativeThread"], "nativeTurn": p["nativeTurn"]}
    if any(scope.get(k) != v for k, v in expected.items()):
        raise Conflict("Captured execution scope changed")
    d = c.execute("SELECT * FROM hb_delivery WHERE id=%s AND agent=%s FOR UPDATE",
                   (scope["deliveryId"], actor["id"])).fetchone()
    if not d or any([
        d["generation"] != scope["generation"], d["claim_id"] != scope["claimId"],
        str(d["event"]) != scope["sourceRequestId"], str(d["host"]) != scope["hostId"],
    ]):
        raise Conflict("Original execution owner changed")
    return scope


def append_activity(c, store, actor, purpose, binding, p):
    v = p["value"]
    if set(v) != {"scopeId", "payload"}:
        raise ValueError("Captured activity fields differ")
    scope = original_scope(c, actor, purpose, binding, p, v["scopeId"])
    payload = normalize(v["payload"])
    if any(payload[k] != scope[k] for k in ("claimId", "generation", "nativeThread", "nativeTurn")):
        raise Conflict("Activity belongs to another captured execution")
    # Internal purpose bookkeeping and model text are not outward communication.
    if payload["item"]["nativeType"] not in ("commandExecution", "fileChange", "planUpdate"):
        raise Denied("Internal context material is not public execution activity")
    from .activity import append_in_transaction
    return append_in_transaction(store, c, actor, scope["deliveryId"], payload, execution_scope=scope)
