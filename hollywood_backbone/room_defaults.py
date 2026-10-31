"""Explicit default room membership for enrolled host representatives.

This is communication permission policy, not agent placement or execution. A
role name alone grants nothing. Existing explicit membership (including a later
revocation) is never overwritten by a default or registration reconciliation.
"""
from uuid import UUID
from .store import Conflict, Denied, json_value


def join(c, authority, room=None):
    query = """INSERT INTO hb_membership(authority,room,principal,can_read,can_post,can_request)
        SELECT r.authority,r.id,d.principal,true,true,true
        FROM hb_room r JOIN hb_room_default_agent d ON d.authority=r.authority
        JOIN hb_principal p ON p.id=d.principal AND p.authority=d.authority
        WHERE r.authority=%s AND NOT r.room_reference AND d.enabled AND p.active
          AND (%s::uuid IS NULL OR r.id=%s::uuid)
        ON CONFLICT(room,principal) DO NOTHING RETURNING room,principal"""
    return len(c.execute(query, (authority, room, room)).fetchall())


def read(store, actor_id):
    with store.pool.connection() as c:
        actor=store.actor(c,actor_id);store.admin(actor)
        rows=c.execute('SELECT * FROM hb_room_default_agent WHERE authority=%s ORDER BY node',
                       (actor['authority'],)).fetchall()
        return json_value({'authorityId':actor['authority'],'defaults':rows})


def change(c, actor, node, payload):
    node=str(UUID(node));principal=str(UUID(payload['agentId']))
    if type(payload['enabled']) is not bool or type(payload['revision']) is not int or payload['revision']<0:
        raise ValueError('Explicit participation and original revision required')
    target=c.execute("SELECT * FROM hb_principal WHERE id=%s AND authority=%s AND kind='agent' AND active FOR SHARE",
                     (principal,actor['authority'])).fetchone()
    if not target or str(target['home_node'])!=node:
        raise Denied('Representative must be the active original agent of this enrolled node')
    if target['identity_reference'] and not c.execute(
            'SELECT 1 FROM hb_federated_agent_grant WHERE principal=%s AND enabled FOR SHARE',(principal,)).fetchone():
        raise Denied('Explicit receiving identity reference required')
    # Lock the stable authority/node key even when no row exists yet.
    c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
              (str(actor['authority'])+':room-default:'+node,))
    old=c.execute('SELECT * FROM hb_room_default_agent WHERE authority=%s AND node=%s FOR UPDATE',
                  (actor['authority'],node)).fetchone()
    if old and str(old['principal'])==principal and old['enabled']==payload['enabled']:
        return json_value({'registration':old,'existingRoomMembershipsAdded':0})
    if (old['revision'] if old else 0)!=payload['revision']:
        raise Conflict('Room representative registration revision changed')
    row=c.execute('''INSERT INTO hb_room_default_agent(authority,node,principal,enabled,created_by)
        VALUES(%s,%s,%s,%s,%s) ON CONFLICT(authority,node) DO UPDATE SET
        principal=EXCLUDED.principal,enabled=EXCLUDED.enabled,revision=hb_room_default_agent.revision+1,
        updated_at=clock_timestamp() RETURNING *''',
        (actor['authority'],node,principal,payload['enabled'],actor['id'])).fetchone()
    added=join(c,actor['authority']) if payload['enabled'] else 0
    return json_value({'registration':row,'existingRoomMembershipsAdded':added})
