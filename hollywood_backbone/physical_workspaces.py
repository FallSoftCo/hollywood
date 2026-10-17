"""Registered target namespaces and per-action protected adapter attestations.

The service verifies registry/grant/source bindings, never remote filesystem facts.
Actual root handles, PathUri/environment routing and no-symlink resolution must be
verified by the captured adapter; hashes and bearer tokens alone are not proof.
"""
from .store import Conflict, Denied, digest, json_value
from .physical_models import CAPABILITY


def root_path(value):
    from .collaboration import canonical_path
    if not value.startswith('/'):
        raise ValueError('Absolute canonical root path required')
    canonical_path(value[1:])
    return value


def configure(c, actor, host, identity, payload):
    if not c.execute('SELECT 1 FROM hb_host WHERE id=%s AND authority=%s', (host, actor['authority'])).fetchone():
        raise Denied('Registered physical host required')
    path = root_path(payload['rootPath'])
    old = c.execute('SELECT * FROM hb_physical_workspace WHERE id=%s FOR UPDATE', (identity,)).fetchone()
    if (old['revision'] if old else 0) != payload['revision']:
        raise Conflict('Physical workspace revision changed')
    if old and (old['authority'] != actor['authority'] or str(old['host']) != host or
                (old['root_path'], old['environment_sha256'], old['root_identity_sha256']) !=
                (path, payload['environmentSha256'], payload['rootIdentitySha256'])):
        raise Conflict('Physical identity is immutable; never move an owned namespace')
    if c.execute('''SELECT 1 FROM hb_physical_workspace WHERE authority=%s AND host=%s AND id<>%s
        AND (root_identity_sha256=%s OR (environment_sha256=%s AND
        (root_path=%s OR left(root_path,length(%s)+1)=%s||'/' OR left(%s,length(root_path)+1)=root_path||'/')))''',
        (actor['authority'], host, identity, payload['rootIdentitySha256'], payload['environmentSha256'], path, path, path, path)).fetchone():
        raise Conflict('Aliased or nested physical roots require the existing namespace')
    row = c.execute('''INSERT INTO hb_physical_workspace(id,authority,host,state,root_path,
        environment_sha256,root_identity_sha256,registration_sha256) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT(id) DO UPDATE SET state=EXCLUDED.state,revision=hb_physical_workspace.revision+1,
        registration_sha256=EXCLUDED.registration_sha256 RETURNING *''',
        (identity, actor['authority'], host, payload['state'], path, payload['environmentSha256'],
         payload['rootIdentitySha256'], payload['registrationSha256'])).fetchone()
    return json_value({'schema': 'hollywood-physical-workspace/v1', 'workspace': row, 'mayApply': False})


def grant(c, actor, workspace, work_id, payload):
    root = c.execute('SELECT * FROM hb_physical_workspace WHERE id=%s AND authority=%s FOR UPDATE',
                     (workspace, actor['authority'])).fetchone()
    work = c.execute('SELECT * FROM hb_work WHERE id=%s AND authority=%s FOR UPDATE', (work_id, actor['authority'])).fetchone()
    if not root or not work:
        raise Denied('Canonical workspace and work required')
    if root['revision'] != payload['workspaceRevision'] or work['revision'] != payload['workRevision']:
        raise Conflict('Workspace/work revision changed')
    context = c.execute('SELECT * FROM hb_agent_context WHERE principal=%s', (work['owner'],)).fetchone()
    if not context:
        raise Denied('Captured persistent owner context required')
    old = c.execute('SELECT * FROM hb_physical_grant WHERE workspace=%s AND work=%s FOR UPDATE', (workspace, work_id)).fetchone()
    if (old['revision'] if old else 0) != payload['revision']:
        raise Conflict('Physical grant revision changed')
    row = c.execute('''INSERT INTO hb_physical_grant(workspace,work,owner,context_id,execution_host,
        work_revision,state) VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(workspace,work) DO UPDATE SET
        owner=EXCLUDED.owner,context_id=EXCLUDED.context_id,execution_host=EXCLUDED.execution_host,
        work_revision=EXCLUDED.work_revision,state=EXCLUDED.state,revision=hb_physical_grant.revision+1 RETURNING *''',
        (workspace, work_id, work['owner'], context['context_id'], context['host'], work['revision'], payload['state'])).fetchone()
    return json_value({'schema': 'hollywood-physical-grant/v1', 'grant': row, 'mayApply': False})


def registered(c, authority, source, work_id, target, cleanup=False):
    if not target:
        raise Conflict('Legacy implicit target has no registered physical authority')
    root = c.execute('SELECT * FROM hb_physical_workspace WHERE id=%s AND authority=%s',
                     (target['workspaceId'], authority)).fetchone()
    access = c.execute('''SELECT g.*,w.owner AS current_owner,w.revision AS current_work_revision
        FROM hb_physical_grant g JOIN hb_work w ON w.id=g.work WHERE g.workspace=%s AND g.work=%s''',
        (target['workspaceId'], work_id)).fetchone()
    if not root or not access:
        raise Denied('Unregistered or unauthorized physical target')
    if (root['revision'] != target['workspaceRevision'] or access['revision'] != target['grantRevision']
            or root['root_identity_sha256'] != target['rootIdentitySha256']
            or root['environment_sha256'] != target['environmentSha256']):
        raise Conflict('Physical root/environment/grant changed')
    if (str(access['owner']) != source['agentId'] or access['owner'] != access['current_owner']
            or access['context_id'] != source['contextId'] or str(access['execution_host']) != source['hostId']):
        raise Denied('Physical grant differs from captured execution context')
    if not cleanup and (root['state'] != 'active' or access['state'] != 'active'
                       or access['work_revision'] != access['current_work_revision']):
        raise Conflict('Physical target authority is revoked or changed')
    return root, access


def paths_for(c, payload):
    command = payload.get('command')
    if command is None:  # independently stopped observation
        permit = c.execute('SELECT plan,plan_revision FROM hb_edit_permit WHERE id=%s', (payload['permitId'],)).fetchone()
        version = c.execute('SELECT manifest FROM hb_edit_plan_revision WHERE plan=%s AND revision=%s',
                            (permit['plan'], permit['plan_revision'])).fetchone() if permit else None
        return [f['relativePath'] for f in version['manifest']['files']] if version else []
    if 'claimId' in command:
        if command['action'] == 'acquire':
            return [command['relativePath']]
        claim = c.execute('SELECT relative_path FROM hb_path_claim WHERE id=%s', (command['claimId'],)).fetchone()
        return [claim['relative_path']] if claim else []
    if command['action'] == 'consent':
        return list({a['relativePath'] for a in command['artifacts'] + command['imports']})
    if command['action'] == 'revoke':
        vote = c.execute('SELECT artifacts,imports FROM hb_edit_consent WHERE plan=%s AND plan_revision=%s AND work=%s',
                         (command['planId'], command['planRevision'], command['workId'])).fetchone()
        return list({a['relativePath'] for a in vote['artifacts'] + vote['imports']}) if vote else []
    manifest = command.get('manifest')
    if manifest is None:
        row = c.execute('SELECT manifest FROM hb_edit_plan_revision WHERE plan=%s AND revision=%s',
                         (command['planId'], command['planRevision'])).fetchone()
        manifest = row['manifest'] if row else {'files': []}
    return [f['relativePath'] for f in manifest['files']]


def attest(c, relay, source, binding, payload, work_id=None, cleanup=False):
    from .collaboration import bridge, canonical_path
    policy = bridge(c, relay, source['hostId'], payload['bridgeRevision'])
    if policy['protocol'] != CAPABILITY:
        raise Denied('Registered physical target profile required; implicit context roots are disabled')
    target = payload['target']
    if target['actionDigest'] != digest({k: v for k, v in payload.items() if k != 'target'}):
        raise Conflict('Target attestation must bind exact operation/source/executor/command')
    root, access = registered(c, relay['authority'], source, work_id or payload['command']['workId'], target, cleanup)
    if not cleanup and (payload.get('command') or {}).get('action') not in ('release', 'inspect'):
        if c.execute("SELECT 1 FROM hb_path_claim WHERE authority=%s AND state<>'released' AND target IS NULL LIMIT 1",
                     (relay['authority'],)).fetchone():
            raise Conflict('Legacy unregistered ownership remains held for explicit reviewed cleanup')
    expected = set(paths_for(c, payload))
    paths = target['paths']
    if len(paths) != len(expected) or {p['relativePath'] for p in paths} != expected:
        raise Conflict('Exact per-action resolved target set required')
    for item in paths:
        relative = canonical_path(item['relativePath'])
        if item['resolvedPath'] != root['root_path'] + '/' + relative:
            raise Denied('Resolved target escapes or aliases registered root')
    return {**binding, 'physical_workspace_id': root['id'], 'physical_host': root['host'],
            'physical_target': target, 'physical_grant': access}


def peer_access(c, authority, source, work, workspace):
    """Handoff recipient needs an existing grant to the SAME physical resource."""
    root = c.execute('SELECT * FROM hb_physical_workspace WHERE id=%s AND authority=%s', (workspace, authority)).fetchone()
    access = c.execute('SELECT * FROM hb_physical_grant WHERE workspace=%s AND work=%s', (workspace, work['id'])).fetchone()
    if not root or not access:
        raise Denied('Explicit recipient resource grant required')
    registered(c, authority, source, work['id'], {'workspaceId': str(workspace), 'workspaceRevision': root['revision'],
        'grantRevision': access['revision'], 'rootIdentitySha256': root['root_identity_sha256'],
        'environmentSha256': root['environment_sha256']})


def read(store, actor_id, work_id):
    from .collaboration import bridge
    with store.pool.connection() as c:
        relay = store.actor(c, actor_id)
        work = c.execute('SELECT * FROM hb_work WHERE id=%s AND authority=%s', (work_id, relay['authority'])).fetchone()
        context = c.execute('SELECT * FROM hb_agent_context WHERE principal=%s', (work['owner'],)).fetchone() if work else None
        if not context:
            raise Denied('Canonical owner context unavailable')
        bridge(c, relay, str(context['host']))
        actor = store.actor(c, work['owner'])
        store.room(c, actor, work['room'])
        roots = c.execute('''SELECT w.*,g.revision AS grant_revision,g.work_revision,g.state AS grant_state
            FROM hb_physical_workspace w JOIN hb_physical_grant g ON g.workspace=w.id
            WHERE g.work=%s AND g.owner=%s AND g.context_id=%s AND g.execution_host=%s''',
            (work_id, actor['id'], context['context_id'], context['host'])).fetchall()
        return json_value({'schema': 'hollywood-physical-target-registry/v1', 'workId': work_id,
            'workRevision': work['revision'], 'targets': roots, 'mayApply': False})
