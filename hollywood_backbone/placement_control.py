"""Pause/resume the same enrolled placement without changing owned effects."""
from psycopg.types.json import Jsonb

from .store import Conflict, Denied, json_value


def enrolled(c, actor, principal):
    if not actor['administrator'] and (actor['kind'] != 'agent' or str(actor['id']) != principal):
        raise Denied('Administrator or placement owner required')
    row = c.execute('''SELECT l.* FROM hb_placement l JOIN hb_principal p
        ON p.id=l.agent AND p.authority=l.authority AND p.kind='agent'
        WHERE l.agent=%s AND l.authority=%s''', (principal, actor['authority'])).fetchone()
    if not row:
        raise Denied('Existing enrolled placement required')
    return row


def view(row):
    return json_value({'schema': 'hollywood-placement/v1', 'authorityId': row['authority'],
        'agentId': row['agent'], 'hostId': row['host'], 'placement': row,
        'state': 'enabled' if row['enabled'] else 'paused', 'newExecutionAdmitted': False,
        'drainVerified': False, 'mayApply': False})


def read(store, actor_id, principal):
    with store.pool.connection() as c:
        actor = store.actor(c, actor_id)
        return view(enrolled(c, actor, principal))


def change(c, actor, principal, payload):
    # Administrative transport already holds the exclusive authority fence and
    # validates current provenance before original operation receipt recovery.
    row = enrolled(c, actor, principal)
    row = c.execute('SELECT * FROM hb_placement WHERE agent=%s AND authority=%s FOR UPDATE',
                    (principal, actor['authority'])).fetchone()
    if str(row['host']) != payload['hostId']:
        raise Conflict('Enrolled placement host changed; transfer is not pause/resume')
    if row['revision'] != payload['revision']:
        raise Conflict('Placement revision changed')
    before = json_value(row)
    updated = c.execute('''UPDATE hb_placement SET enabled=%s,revision=revision+1
        WHERE agent=%s AND authority=%s RETURNING *''',
        (payload['enabled'], principal, actor['authority'])).fetchone()
    c.execute('''INSERT INTO hb_placement_state_revision
        (authority,agent,host,revision,actor,operation,before_state,after_state)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s)''',
        (actor['authority'], principal, updated['host'], updated['revision'], actor['id'],
         payload['operationId'], Jsonb(before), Jsonb(json_value(updated))))
    return view(updated)
