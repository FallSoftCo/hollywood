
from .store import Denied


def resolve(store, c, actor, room, payload):
    """Record scoped planning intent without inventing a human request or turn."""
    source = store.readable_event(c, actor, payload["sourceEventId"])
    if source["kind"] != "request":
        raise Denied("Goal requires an addressed source request")
    if source["audience"] != "room":
        raise Denied("Room-visible goal requires room-visible source")
    if not actor["administrator"] and actor["id"] not in (source["author"], source["recipient"]):
        raise Denied("Goal creation must belong to the addressed exchange")
    origin = c.execute("SELECT kind FROM hb_principal WHERE id=%s AND authority=%s",
                       (source["author"], actor["authority"])).fetchone()
    if not origin or origin["kind"] not in ("human", "agent"):
        raise Denied("Goal origin requires an authenticated human or agent request")
    if str(source["room"]) == room:
        return source
    if payload.get("publishScopedIntent") is not True:
        raise Denied("Cross-room goal requires explicit publication of scoped intent")
    # Publish only selected planning intent. Reading the origin does not grant
    # other room participants access to its text or original room.
    identity = store.event(c, actor, room, "message",
        "Goal planned: " + payload["title"], recipient=source["recipient"],
        audience="room", source=source["id"],
        data={"type": "goalOrigin", "goalId": payload["id"],
              "originRequestId": str(source["id"]), "originAuthorKind": origin["kind"],
              "originRoomId": str(source["room"]), "scope": "explicit-planning-intent/v1"})
    return store.readable_event(c, actor, identity)
