"""Bounded resource metadata and explicit routing; never mutation authority.

All HTTP reads share the existing authority fence. Each page reauthenticates;
the cursor is a workspace UUID, not a frozen multi-page snapshot.
"""
import json

from psycopg.types.json import Jsonb

from .store import Conflict, Denied, json_value

MAX_BYTES = 32768


def page_bound(limit):
    if type(limit) is not int or not 1 <= limit <= 8:
        raise ValueError('Resource page must contain 1..8 rows')


def bounded(value):
    value = json_value(value)
    if len(json.dumps(value, ensure_ascii=True).encode('ascii')) > MAX_BYTES:
        raise Conflict('Resource metadata response exceeds byte limit')
    return value


def subject(c, actor, principal, host):
    # Scope check precedes subject lookup, including for service principals.
    if not actor['administrator'] and (actor['kind'] != 'agent' or str(actor['id']) != principal):
        raise Denied('Administrator or resource owner required')
    agent = c.execute("SELECT * FROM hb_principal WHERE id=%s AND authority=%s AND kind='agent'",
                      (principal, actor['authority'])).fetchone()
    enrolled = c.execute('SELECT 1 FROM hb_host WHERE id=%s AND authority=%s',
                         (host, actor['authority'])).fetchone()
    if not agent or not enrolled:
        raise Denied('Registered agent and execution host required')
    return agent


def default_view(c, authority, principal, host):
    pointer = c.execute('''SELECT * FROM hb_resource_default
        WHERE authority=%s AND principal=%s AND execution_host=%s''',
        (authority, principal, host)).fetchone()
    state, reason, resource = 'missing', None, None
    if pointer:
        state = pointer['state']
        if state == 'selected':
            invalidated = c.execute('''SELECT reason FROM hb_resource_default_hold
                WHERE principal=%s AND execution_host=%s AND revision=%s''',
                (principal, host, pointer['revision'])).fetchone()
            root = c.execute('SELECT * FROM hb_physical_workspace WHERE id=%s AND authority=%s',
                             (pointer['workspace'], authority)).fetchone()
            access = c.execute('SELECT * FROM hb_physical_agent_grant WHERE workspace=%s AND principal=%s',
                               (pointer['workspace'], principal)).fetchone()
            active = c.execute("SELECT 1 FROM hb_principal WHERE id=%s AND authority=%s AND kind='agent' AND active",
                               (principal, authority)).fetchone()
            placed = c.execute('SELECT 1 FROM hb_placement WHERE agent=%s AND host=%s AND enabled',
                               (principal, host)).fetchone()
            if invalidated:
                reason = invalidated['reason']
            elif not active or not placed:
                reason = 'participation-unavailable'
            elif not root or not access:
                reason = 'resource-unavailable'
            elif root['state'] != 'active' or access['state'] != 'active' or not access['can_read'] or not access['can_write']:
                reason = 'access-unavailable'
            elif str(root['host']) != str(host) or str(access['execution_host']) != str(host):
                reason = 'execution-host-incompatible'
            elif root['revision'] != pointer['workspace_revision'] or access['revision'] != pointer['grant_revision']:
                reason = 'selection-revisions-changed'
            if reason:
                state = 'held'
            else:
                resource = {'workspace': root, 'grant': access}
    return {'schema': 'hollywood-agent-resource-default/v1', 'authorityId': authority,
            'agentId': principal, 'executionHost': host, 'selection': pointer,
            'selectionRevision': pointer['revision'] if pointer else 0,
            'effectiveState': state, 'holdReason': reason, 'resource': resource, 'mayApply': False}


def select(c, actor, principal, host, payload):
    agent = subject(c, actor, principal, host)
    old = c.execute('SELECT * FROM hb_resource_default WHERE principal=%s AND execution_host=%s FOR UPDATE',
                    (principal, host)).fetchone()
    if (old['revision'] if old else 0) != payload['revision']:
        raise Conflict('Resource default revision changed')
    if payload['state'] == 'selected':
        root = c.execute('SELECT * FROM hb_physical_workspace WHERE id=%s AND authority=%s',
                         (payload['workspaceId'], actor['authority'])).fetchone()
        grant = c.execute('SELECT * FROM hb_physical_agent_grant WHERE workspace=%s AND principal=%s',
                          (payload['workspaceId'], principal)).fetchone()
        # Configure routing while paused, but default_view remains held until
        # explicit resume. Existing invalidated revisions never revive.
        placed = c.execute('SELECT 1 FROM hb_placement WHERE agent=%s AND host=%s',
                           (principal, host)).fetchone()
        if not root or not grant or not agent['active'] or not placed:
            raise Denied('Active resource access and registered placement required for routing setup')
        if (root['state'] != 'active' or grant['state'] != 'active' or not grant['can_read'] or not grant['can_write']
                or str(root['host']) != host or str(grant['execution_host']) != host):
            raise Denied('Active local read/write resource access required')
        if root['revision'] != payload['workspaceRevision'] or grant['revision'] != payload['grantRevision']:
            raise Conflict('Resource selection revisions changed')
    row = c.execute('''INSERT INTO hb_resource_default
        (authority,principal,execution_host,state,workspace,workspace_revision,grant_revision)
        VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(principal,execution_host) DO UPDATE SET
        state=EXCLUDED.state,workspace=EXCLUDED.workspace,workspace_revision=EXCLUDED.workspace_revision,
        grant_revision=EXCLUDED.grant_revision,revision=hb_resource_default.revision+1,
        updated_at=clock_timestamp() RETURNING *''',
        (actor['authority'], principal, host, payload['state'], payload['workspaceId'],
         payload['workspaceRevision'], payload['grantRevision'])).fetchone()
    c.execute('''INSERT INTO hb_resource_default_revision
        (authority,principal,execution_host,revision,actor,operation,snapshot)
        VALUES(%s,%s,%s,%s,%s,%s,%s)''',
        (actor['authority'], principal, host, row['revision'], actor['id'], payload['operationId'], Jsonb(json_value(row))))
    return bounded(default_view(c, actor['authority'], principal, host))


def roots(store, actor_id, host=None, after=None, limit=4):
    page_bound(limit)
    with store.pool.connection() as c:
        actor = store.actor(c, actor_id)
        store.admin(actor)
        rows = c.execute('''SELECT * FROM hb_physical_workspace WHERE authority=%s
            AND (%s::uuid IS NULL OR host=%s::uuid) AND (%s::uuid IS NULL OR id>%s::uuid)
            ORDER BY id LIMIT %s''', (actor['authority'], host, host, after, after, limit + 1)).fetchall()
        return bounded({'schema': 'hollywood-resource-roots/v1', 'authorityId': actor['authority'],
                        'workspaces': rows[:limit], 'nextCursor': rows[limit - 1]['id'] if len(rows) > limit else None,
                        'limit': limit, 'mayApply': False})


def resources(store, actor_id, principal, host, after=None, limit=4):
    page_bound(limit)
    with store.pool.connection() as c:
        actor = store.actor(c, actor_id)
        subject(c, actor, principal, host)
        rows = c.execute('''SELECT row_to_json(w) AS workspace,row_to_json(g) AS grant
            FROM hb_physical_workspace w JOIN hb_physical_agent_grant g ON g.workspace=w.id
            WHERE w.authority=%s AND g.principal=%s AND g.execution_host=%s
            AND (%s::uuid IS NULL OR w.id>%s::uuid)
            AND (%s OR (w.state='active' AND g.state='active' AND g.can_read
              AND EXISTS(SELECT 1 FROM hb_placement p WHERE p.agent=g.principal AND p.host=g.execution_host AND p.enabled)))
            ORDER BY w.id LIMIT %s''',
            (actor['authority'], principal, host, after, after, actor['administrator'], limit + 1)).fetchall()
        return bounded({'schema': 'hollywood-agent-resources/v1', 'authorityId': actor['authority'],
            'agentId': principal, 'executionHost': host, 'resources': rows[:limit],
            'default': default_view(c, actor['authority'], principal, host),
            'nextCursor': rows[limit - 1]['workspace']['id'] if len(rows) > limit else None,
            'limit': limit, 'mayApply': False})


def read_default(store, actor_id, principal, host):
    with store.pool.connection() as c:
        actor = store.actor(c, actor_id)
        subject(c, actor, principal, host)
        return bounded(default_view(c, actor['authority'], principal, host))


def delivery_default(store, actor_id, delivery):
    from .collaboration import bridge
    with store.pool.connection() as c:
        actor = store.actor(c, actor_id)
        row = c.execute('SELECT * FROM hb_delivery WHERE id=%s AND authority=%s',
                        (delivery, actor['authority'])).fetchone()
        if not row or not row['host']:
            raise Denied('Captured execution host required')
        bridge(c, actor, str(row['host']))
        # Derive identity from the canonical delivery for both ordinary and work
        # requests. No default grants, work grants or admission are manufactured.
        return bounded({'schema': 'hollywood-delivery-resource-default/v1', 'deliveryId': delivery,
            'agentId': row['agent'], 'executionHost': row['host'],
            'default': default_view(c, actor['authority'], row['agent'], row['host']), 'mayApply': False})
