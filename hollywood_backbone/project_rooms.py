"""Human project creation is not delegated enterprise administration."""
from .store import Denied

PROTOCOL = "hollywood-project-rooms/v1"


def controls(actor, rooms):
    sources = [str(r["id"]) for r in rooms if actor["administrator"] or
               (r.get("memberCanRead") and r.get("memberCanRequest"))]
    allowed = actor["kind"] == "human" and bool(sources)
    return {"protocol": PROTOCOL, "authorityId": str(actor["authority"]),
            "principalId": str(actor["id"]), "sourceRoomIds": sources,
            "createRoom": {"allowed": allowed,
                           "policy": "administrator" if actor["administrator"] else "source-room-request-owner",
                           "reason": None if allowed else "REQUESTABLE_SOURCE_ROOM_REQUIRED"}}


def authorize(store, c, actor, source, owner):
    if actor["kind"] != "human" or not source:
        raise Denied("Human room creation needs an explicit requestable source room")
    room = store.room(c, actor, source, "can_read")
    store.room(c, actor, source, "can_request")
    # Source proof is canonical and local to this accepting node, not a
    # client-side actor list or a foreign/historical room reference.
    target = c.execute("""SELECT p.id FROM hb_principal p JOIN hb_membership m
        ON m.principal=p.id AND m.room=%s WHERE p.id=%s AND p.authority=%s
        AND p.kind='agent' AND p.active AND m.can_read FOR SHARE OF p,m""",
        (room["id"], owner, actor["authority"])).fetchone()
    if not target:
        raise Denied("Project owner must be an active readable agent in the source room")
