"""Read-only public attribution from enrolled canonical host identity.

No principal rename, deployment UUID map, capabilities, credentials or implicit
placement. Unknown host stays unknown. Event bytes/receipts are never enriched.
"""
from .store import json_value


def descriptor(c, principal):
    row = c.execute("""SELECT p.id,p.name,p.kind,p.home_node,h.id AS host_id,h.name AS host_name,o.room AS origin_room
        FROM hb_principal p LEFT JOIN hb_placement pl ON pl.agent=p.id AND pl.authority=p.authority
        LEFT JOIN hb_host h ON h.id=coalesce(pl.host,p.home_node) AND h.authority=p.authority
        LEFT JOIN hb_room_agent_origin o ON o.agent=p.id
        WHERE p.id=%s""", (principal,)).fetchone()
    if not row:
        return None
    return json_value({"version": 1, "agentId": row["id"], "name": row["name"],
        "kind": row["kind"], "homeNodeId": row["home_node"], "originRoomId": row['origin_room'],
        "host": {"id": row["host_id"], "name": row["host_name"]} if row["host_id"] else None})
