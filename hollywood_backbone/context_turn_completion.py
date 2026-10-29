"""Negotiate and settle a captured purpose batch; no task or model is started."""

SETTLEMENT_ACTIONS = {
    "accept", "answer", "observe", "initiative", "peer_result", "amend",
    "rebuild_request", "review_writer_outcome", "complete", "wait_peers", "wait_goals", "wait_human", "publish", "suppress",
}
SETTLED = {
    "reader": {"handled", "unavailable", "cancelled"},
    "writer": {"complete", "waiting", "cancelled"},
    "responder": {"published", "suppressed", "unavailable", "cancelled"},
}


def settle(c, actor, purpose, binding, p, result):
    """Close callbacks only when every original captured material has settled.

    Raw later arrivals remain pending for the next inference. This is included
    in the original tool operation transaction/receipt, not an auxiliary read
    which could misclassify an already accepted mutation as a failed send.
    """
    if (binding.get("tool_completion", 0) != 1
            or p["action"] not in SETTLEMENT_ACTIONS
            or not binding["active_turn"] or not binding["accepting"]):
        return result
    if p["action"] == "rebuild_request":
        if (result.get("requested") is not True
                or result.get("requestOperationId") != p["operationId"]
                or result.get("nativeThread") != p["nativeThread"]):
            return result
        c.execute("""UPDATE hb_purpose_context SET accepting=false
            WHERE agent=%s AND purpose=%s AND active_turn=%s""",
            (actor["id"], purpose, p["nativeTurn"]))
        return {**result, "workCompletionInferred": False, "inferenceControl": {
            "nativeThread": p["nativeThread"], "nativeTurn": p["nativeTurn"],
            "disposition": "complete"}}
    ids = binding["active_material"]
    if not ids:
        return result
    rows = c.execute("""SELECT id,kind,state FROM hb_context_material
        WHERE agent=%s AND id=ANY(%s::uuid[]) FOR SHARE""",
        (actor["id"], ids)).fetchall()
    kind = {"reader": "input", "writer": "work", "responder": "need"}[purpose]
    if (len(rows) != len(ids) or any(row["kind"] != kind
            or row["state"] not in SETTLED[purpose] for row in rows)):
        return result
    c.execute("""UPDATE hb_purpose_context SET accepting=false
        WHERE agent=%s AND purpose=%s AND active_turn=%s""",
        (actor["id"], purpose, p["nativeTurn"]))
    return {**result, "inferenceControl": {
        "nativeThread": p["nativeThread"], "nativeTurn": p["nativeTurn"],
        "disposition": "complete",
    }}
