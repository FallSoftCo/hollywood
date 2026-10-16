"""Reply obligations are explicit communication, not recurring model supervision."""

import json
from .store import uid


def completion(store, c, actor, request, delivery, result, state):
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
    # Result and one response obligation commit with the original transition.
    # The generated response has no expectsReply flag, so it cannot ping-pong.
    event = store.event(
        c,
        actor,
        request["room"],
        "request",
        json.dumps(
            {
                "type": "peer_completion",
                "requestId": str(request["id"]),
                "resultEventId": str(result),
                "executionState": state,
                "originRequestId": (
                    str(request["source"]) if request["source"] else None
                ),
            }
        ),
        audience=request["audience"],
        recipient=target["id"],
        source=result,
        data={
            "type": "peerCompletion",
            "replyToRequest": str(request["id"]),
            "replyToDelivery": str(delivery["id"]),
        },
    )
    identity = uid()
    c.execute(
        "INSERT INTO hb_delivery(id,authority,event,agent) VALUES(%s,%s,%s,%s)",
        (identity, actor["authority"], event, target["id"]),
    )
    return identity
