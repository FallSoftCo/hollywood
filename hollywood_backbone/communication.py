"""Reply obligations are explicit communication, not recurring model supervision."""

import json


def completion(store, c, actor, request, delivery, result, state, *, result_kind="native_execution"):
    if (
        not request["data"].get("expectsReply")
        or state == "cancelled"
        or delivery["cancellation_requested"]
    ):
        return None
    if (
        request["source"]
        and c.execute(
            "SELECT 1 FROM hb_delivery WHERE event=%s AND cancellation_requested",
            (request["source"],),
        ).fetchone()
    ):
        return None
    target = c.execute(
        "SELECT * FROM hb_principal WHERE id=%s AND authority=%s AND active AND kind='agent'",
        (request["author"], actor["authority"]),
    ).fetchone()
    if not target:
        return None
    try:
        store.room(c, target, request["room"])
    except Exception as error:
        from .store import Denied

        if isinstance(error, Denied):
            return None
        raise
    # A result notice commits with the original transition. It is communication,
    # never another execution request/delivery or acknowledgement obligation.
    # Native Readers receive the same typed input; external agents use the inbox.
    event = store.event(
        c,
        actor,
        request["room"],
        "result",
        json.dumps(
            {
                "type": "peer_completion",
                "requestId": str(request["id"]),
                "resultEventId": str(result),
                "executionState": state if result_kind == "native_execution" else "not_attested",
                **({"communicationState": "answered", "resultKind": result_kind}
                   if result_kind != "native_execution" else {}),
                "originRequestId": (
                    str(request["source"]) if request["source"] else None
                ),
            }
        ),
        audience=request["audience"],
        recipient=target["id"],
        source=result,
        data={
            **({"resultKind": result_kind, "communicationState": "answered",
                "executionState": "not_attested"} if result_kind != "native_execution" else {}),
            "type": "peerCompletion",
            "communicationVersion": 2,
            "replyToRequest": str(request["id"]),
            "replyToDelivery": str(delivery["id"]),
        },
    )
    from .context_system import ingest
    ingest(c, target["id"], event)
    return event
