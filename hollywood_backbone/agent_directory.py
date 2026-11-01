"""Administrator inventory of agent identities, including unroomed principals."""

from uuid import UUID
from .store import json_value


def read(store, actor_id, after=None, limit=50):
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("Agent directory page must contain 1..100 entries")
    after = str(UUID(after)) if after is not None else None
    with store.pool.connection() as c:
        actor = store.actor(c, actor_id)
        store.admin(actor)
        rows = c.execute(
            """SELECT id,authority,name,kind,administrator,active
               FROM hb_principal WHERE authority=%s AND kind='agent'
                 AND (%s::uuid IS NULL OR id>%s::uuid)
               ORDER BY id LIMIT %s""",
            (actor["authority"], after, after, limit + 1),
        ).fetchall()
        page = rows[:limit]
        return json_value({
            "schema": "hollywood-agent-directory/v1",
            "authorityId": actor["authority"],
            "agents": page,
            "nextAfter": str(page[-1]["id"]) if len(rows) > limit else None,
            "limit": limit,
        })


def visible_room_agents(store, actor_id, after=None, limit=100):
    """Public identity metadata of active agents in the viewer's readable rooms.

    This is not administrator inventory: unroomed/hidden/inactive identities and
    credentials stay absent. Membership is checked at read time; federation can
    subsequently retain only identities canonical at the responding node.
    """
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("Visible agent page must contain 1..100 entries")
    after = str(UUID(after)) if after is not None else None
    with store.pool.connection() as c:
        actor = store.actor(c, actor_id)
        rows = c.execute(
            """SELECT p.id,p.authority,p.name,p.kind,p.administrator,p.active
               FROM hb_principal p WHERE p.authority=%s AND p.kind='agent'
               AND p.active AND (%s::uuid IS NULL OR p.id>%s::uuid)
               AND EXISTS (
                   SELECT 1 FROM hb_room r
                   JOIN hb_membership own ON own.room=r.id AND own.principal=%s
                   WHERE r.authority=p.authority AND NOT r.room_reference
                   AND own.can_read AND (r.owner=p.id OR EXISTS (
                       SELECT 1 FROM hb_membership peer
                       WHERE peer.room=r.id AND peer.principal=p.id AND peer.can_read)))
               ORDER BY p.id LIMIT %s""",
            (actor["authority"], after, after, actor["id"], limit + 1),
        ).fetchall()
        page = rows[:limit]
        return json_value({"agents": page,
            "nextAfter": str(page[-1]["id"]) if len(rows) > limit else None})
