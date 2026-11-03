"""One canonical current-participant classification for actors/fanout/native view.

Membership/read history is distinct from current room representation. The view
also retains not-yet-enrolled desired IDs; it never creates execution ownership.
"""
from .store import json_value,digest


def current(c,room):
    return {str(r['principal']) for r in c.execute('SELECT principal FROM hb_room_current_participant WHERE room=%s',(room,)).fetchall()}


def prior(c,room,*,exclude_desired=True):
    return json_value(c.execute('''SELECT p.id AS "agentId",p.home_node AS "homeNodeId",
        m.revision AS "membershipRevision",m.can_post AS "canPost",m.can_request AS "canRequest"
        FROM hb_membership m JOIN hb_principal p ON p.id=m.principal
        WHERE m.room=%s AND m.can_read AND p.active AND p.kind='agent'
        AND (NOT %s OR NOT EXISTS(SELECT 1 FROM hb_room_agent_initialization i WHERE i.room=m.room AND i.agent=p.id))
        ORDER BY p.id FOR SHARE OF m''',(room,exclude_desired)).fetchall())


def snapshot(c,room,*,exclude_desired=True):
    rows=prior(c,room,exclude_desired=exclude_desired)
    return {'participants':rows,'sha256':digest(rows)}
