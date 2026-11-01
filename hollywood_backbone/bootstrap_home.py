"""Bind a new node bootstrap; never move an existing room or agent home."""
from uuid import UUID
from .store import Conflict


def bind(c, authority, plan):
    if "nodeId" not in plan:
        return
    node = str(UUID(plan["nodeId"]))
    principals = [plan["credentials"][name]["principalId"] for name in ("Owner", "Global")]
    rows = c.execute(
        "SELECT id,authority,home_node,identity_reference FROM hb_principal "
        "WHERE id=ANY(%s::uuid[]) FOR UPDATE", (principals,),
    ).fetchall()
    room = c.execute(
        "SELECT authority,home_node,room_reference FROM hb_room WHERE id=%s FOR UPDATE",
        (plan["globalRoomId"],),
    ).fetchone()
    invalid_principal = any(
        str(row["authority"]) != authority or row["identity_reference"]
        or row["home_node"] is not None and str(row["home_node"]) != node
        for row in rows
    )
    if (len(rows) != 2 or not room or invalid_principal
            or str(room["authority"]) != authority or room["room_reference"]
            or room["home_node"] is not None and str(room["home_node"]) != node):
        raise Conflict("Bootstrap cannot transfer existing node ownership")
    unbound = any(row["home_node"] is None for row in rows) or room["home_node"] is None
    if unbound and c.execute(
        "SELECT 1 FROM hb_event WHERE authority=%s LIMIT 1", (authority,),
    ).fetchone():
        raise Conflict("Active legacy bootstrap requires explicit home migration")
    c.execute(
        "UPDATE hb_principal SET home_node=%s WHERE id=ANY(%s::uuid[]) AND home_node IS NULL",
        (node, principals),
    )
    c.execute(
        "UPDATE hb_room SET home_node=%s WHERE id=%s AND home_node IS NULL",
        (node, plan["globalRoomId"]),
    )
