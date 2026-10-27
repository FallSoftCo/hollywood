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
