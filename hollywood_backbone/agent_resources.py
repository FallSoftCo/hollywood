"""Administrator-controlled physical-resource access independent of business work.

No grant means no access. Registration and actual OS/root-handle verification
remain distinct; adding a principal grant never changes bridge enrollment.
"""
from .store import Conflict, Denied, json_value


def grant(c, actor, workspace, principal, payload):
    root = c.execute('SELECT * FROM hb_physical_workspace WHERE id=%s AND authority=%s FOR UPDATE',
                     (workspace, actor['authority'])).fetchone()
    agent = c.execute("SELECT * FROM hb_principal WHERE id=%s AND authority=%s AND kind='agent'",
                      (principal, actor['authority'])).fetchone()
    # Setup metadata is allowed while paused; registered() still requires an
    # enabled placement for mutation. Enrollment alone never admits execution.
    placed = c.execute('SELECT 1 FROM hb_placement WHERE agent=%s AND host=%s',
                       (principal, payload['executionHost'])).fetchone()
    host = c.execute('SELECT 1 FROM hb_host WHERE id=%s AND authority=%s',
                     (payload['executionHost'], actor['authority'])).fetchone()
    if not root or not agent or not host:
        raise Denied('Registered root, host and agent required')
    if payload['state'] == 'active' and (not agent['active'] or not placed):
        raise Denied('Active agent and registered placement required for access setup')
    if root['revision'] != payload['workspaceRevision']:
        raise Conflict('Physical root revision changed')
    old = c.execute('SELECT * FROM hb_physical_agent_grant WHERE workspace=%s AND principal=%s FOR UPDATE',
                    (workspace, principal)).fetchone()
    if (old['revision'] if old else 0) != payload['revision']:
        raise Conflict('Agent resource grant revision changed')
    row = c.execute('''INSERT INTO hb_physical_agent_grant
        (workspace,principal,execution_host,state,can_read,can_write) VALUES(%s,%s,%s,%s,%s,%s)
        ON CONFLICT(workspace,principal) DO UPDATE SET execution_host=EXCLUDED.execution_host,
        state=EXCLUDED.state,can_read=EXCLUDED.can_read,can_write=EXCLUDED.can_write,
        revision=hb_physical_agent_grant.revision+1 RETURNING *''',
        (workspace, principal, payload['executionHost'], payload['state'], payload['canRead'], payload['canWrite'])).fetchone()
    return json_value({'schema': 'hollywood-physical-agent-grant/v1', 'grant': row, 'mayApply': False})


def registered(c, authority, source, target, cleanup=False):
    root = c.execute('SELECT * FROM hb_physical_workspace WHERE id=%s AND authority=%s',
                     (target['workspaceId'], authority)).fetchone()
    access = c.execute('SELECT * FROM hb_physical_agent_grant WHERE workspace=%s AND principal=%s',
                       (target['workspaceId'], source['agentId'])).fetchone()
    if not root or not access:
        raise Denied('Explicit ordinary agent resource access required')
    if (root['revision'] != target['workspaceRevision'] or access['revision'] != target['grantRevision']
            or root['root_identity_sha256'] != target['rootIdentitySha256']
            or root['environment_sha256'] != target['environmentSha256']):
        raise Conflict('Physical root/environment/agent grant changed')
    if str(access['execution_host']) != source['hostId']:
        raise Denied('Agent resource access belongs to another execution host')
    placed = c.execute('SELECT 1 FROM hb_placement WHERE agent=%s AND host=%s AND enabled',
                       (source['agentId'], source['hostId'])).fetchone()
    if not cleanup and (not placed or root['state'] != 'active' or access['state'] != 'active'
                        or not access['can_read'] or not access['can_write']):
        raise Denied('Ordinary resource access revoked or insufficient')
    return root, access


def delivery(store, c, actor, source):
    request = store.readable_event(c, actor, source['requestId'])
    # Editing mode is decided by actual file ownership, not business allocation.
    # The mutation path separately derives/pins current canonical Work authority.
    return request


def read(store, actor_id, delivery_id):
    from .collaboration import bridge
    with store.pool.connection() as c:
        relay = store.actor(c, actor_id)
        d = c.execute('SELECT * FROM hb_delivery WHERE id=%s AND authority=%s',
                      (delivery_id, relay['authority'])).fetchone()
        if not d or not d['host']:
            raise Denied('Captured execution host required')
        bridge(c, relay, str(d['host']))
        actor = store.actor(c, d['agent'])
        request = delivery(store, c, actor, {'requestId': str(d['event'])})
        store.room(c, actor, request['room'], 'can_post')
        roots = c.execute('''SELECT w.*,g.revision AS grant_revision,g.state AS grant_state,
            g.execution_host,g.can_read,g.can_write FROM hb_physical_workspace w
            JOIN hb_physical_agent_grant g ON g.workspace=w.id
            WHERE w.authority=%s AND g.principal=%s AND g.execution_host=%s''',
            (relay['authority'], actor['id'], d['host'])).fetchall()
        return json_value({'schema': 'hollywood-agent-physical-target-registry/v1',
                           'deliveryId': delivery_id, 'principalId': actor['id'],
                           'targets': roots, 'mayApply': False})
