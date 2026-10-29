"""Explicit review of a failed nonexecuting Reader batch; no task/effect replay."""
from .store import Conflict, Denied, json_value
from .context_system import change

SCHEMA = "lax-reader-failure-review/v1"

def acknowledge(store, c, actor, purpose, binding, p):
    v = p["value"]
    if purpose != "reader":
        raise Denied("Only the Reader owns its interpretation recovery")
    if (set(v) != {"nativeTurn", "nativeItemsSha256", "callbacksReconciled"}
            or v.get("callbacksReconciled") is not True
            or not isinstance(v.get("nativeItemsSha256"), str)
            or len(v["nativeItemsSha256"]) != 64
            or any(x not in "0123456789abcdef" for x in v["nativeItemsSha256"])):
        raise ValueError("Exact reviewed original Reader evidence required")
    if binding["active_turn"] is not None or binding["active_material"]:
        raise Conflict("An active Reader batch cannot be replaced")
    turn = v["nativeTurn"]
    if p["nativeTurn"] != turn:
        raise Conflict("Original reviewed turn differs")
    permit = c.execute("""SELECT * FROM hb_context_permit
        WHERE agent=%s AND purpose='reader' ORDER BY ticket DESC LIMIT 1 FOR UPDATE""",
        (actor["id"],)).fetchone()
    if (not permit or permit["context_id"] != binding["context_id"]
            or permit["native_thread"] != p["nativeThread"]
            or permit["native_turn"] != turn or permit["state"] != "released"
            or permit["terminal_status"] != "failed"):
        raise Conflict("Latest released failed Reader permit required")
    if c.execute("""SELECT 1 FROM hb_context_permit WHERE agent=%s AND purpose='reader'
        AND state IN ('queued','reserved','running')""", (actor["id"],)).fetchone():
        raise Conflict("Another Reader admission is unsettled")
    def original(label):
        row=c.execute("""SELECT result FROM hb_context_operation
            WHERE agent=%s AND purpose='reader' AND operation=%s""",
            (actor["id"], "context-" + label + ":" + turn)).fetchone()
        return row["result"] if row else {}
    begin, end = original("begin"), original("end")
    ids = begin.get("materialIds")
    if (begin.get("nativeTurn") != turn or begin.get("state") != "running"
            or end.get("nativeTurn") != turn or end.get("state") != "failed"
            or not isinstance(ids, list) or not 1 <= len(ids) <= 100
            or len(set(ids)) != len(ids)
            or set(ids) != {str(x) for x in permit["material_ids"]}):
        raise Conflict("Original Reader begin/end/material receipts differ")
    rows=c.execute("""SELECT * FROM hb_context_material WHERE agent=%s
        AND id=ANY(%s::uuid[]) ORDER BY id FOR UPDATE""", (actor["id"], ids)).fetchall()
    if len(rows) != len(ids) or any(x["kind"] != "input" for x in rows):
        raise Conflict("Original nonexecuting input set required")
    continued, unchanged = [], []
    for row in rows:
        if row["state"] != "pending":
            unchanged.append(str(row["id"]))
            continue
        store.readable_event(c, actor, row["source"])
        prior = row["value"].get("readerFailureReview", {})
        if prior.get("nativeTurn") == turn:
            if prior.get("nativeItemsSha256") != v["nativeItemsSha256"]:
                raise Conflict("Original reviewed Reader evidence changed")
            unchanged.append(str(row["id"]))
            continue
        change(c, row, "pending", {**row["value"], "readerFailureReview": {
            "nativeTurn": turn, "nativeItemsSha256": v["nativeItemsSha256"],
            "priorInputRevision": row["revision"]}})
        continued.append(str(row["id"]))
    return json_value({"schema": SCHEMA, "contextId": binding["context_id"],
        "purpose": purpose, "nativeThread": p["nativeThread"], "nativeTurn": turn,
        "nativeItemsSha256": v["nativeItemsSha256"], "failedOriginalRetained": True,
        "continuedInputIds": continued, "unchangedInputIds": unchanged,
        "newExecutionAdmitted": False, "originalInputReposted": False,
        "workCompletionInferred": False})
