"""Explicit drained home transfer; native profiles and work never move here.

Operator-only installation functions, not a user/agent HTTP endpoint. The
operator carries one immutable plan and the read-back source fence receipt to
the destination. Fence-before-accept intentionally permits temporary
unavailability, never two authoritative logins. Lost acknowledgements reconcile
the same plan; ordinary account revocations are not undone by replay.
"""
from copy import deepcopy
from uuid import UUID
from psycopg import sql

from .store import Conflict, Denied, digest, json_value
from .federation_identity import describe, install_reference


def plan(store, administrator, principal, destination):
    original = describe(store, administrator, principal)
    source = original['principal']['home_node']
    destination = str(UUID(destination))
    if source == destination:
        raise Conflict('Agent already belongs to the destination')
    proposed = deepcopy(original)
    proposed['principal']['home_node'] = destination
    proposed['principal']['home_revision'] += 1
    return {'version': 1, 'sourceNodeId': source, 'targetNodeId': destination,
            'original': original, 'proposed': proposed}


def validate(value):
    if (not isinstance(value, dict) or set(value) !=
            {'version', 'sourceNodeId', 'targetNodeId', 'original', 'proposed'}
            or value['version'] != 1):
        raise ValueError('Invalid home transfer plan')
    source, target = str(UUID(value['sourceNodeId'])), str(UUID(value['targetNodeId']))
    original, proposed = value['original'], value['proposed']
    expected = deepcopy(original)
    expected['principal']['home_node'] = target
    expected['principal']['home_revision'] += 1
    if (source == target or original['principal']['home_node'] != source
            or expected != proposed):
        raise Conflict('Home transfer changes more than ownership revision')
    return original, proposed, digest(value)


def fence(store, administrator, value):
    original, proposed, fingerprint = validate(value)
    p = original['principal']
    with store.pool.connection() as c:
        actor = store.actor(c, administrator)
        store.admin(actor)
        row = c.execute('SELECT * FROM hb_principal WHERE id=%s AND authority=%s',
                        (p['id'], actor['authority'])).fetchone()
        if not row:
            raise Denied('Original agent unavailable')
        already = (row['identity_reference']
                   and str(row['home_node']) == value['targetNodeId']
                   and row['home_revision'] == proposed['principal']['home_revision'])
        if already:
            from .federation_identity import resolve_in_transaction
            current=resolve_in_transaction(c,authority_id=p['authority'],
                source_host=proposed['placement']['host'],principal_id=p['id'],
                home_node=value['targetNodeId'],credential_digest=proposed['credentialDigest'])
            if c.execute('SELECT 1 FROM hb_token WHERE principal=%s AND NOT revoked',(p['id'],)).fetchone():
                raise Conflict('Original login is not fenced')
            receipt={'principalId':p['id'],'homeNodeId':value['targetNodeId'],
                     'referenceOnly':True,'grantRevision':current.grant_revision,
                     'activeLocalCredential':False,'workerCreated':False}
        else:
            receipt=None
    if receipt is None:
        receipt = install_reference(store, administrator, proposed,
            expected_local_home_revision=p['home_revision'],
            expected_source_descriptor=original)
    return {**receipt, 'version': 1, 'sourceNodeId': value['sourceNodeId'],
            'targetNodeId': value['targetNodeId'], 'planDigest': fingerprint,
            'homeRevision': proposed['principal']['home_revision']}


def accept(store, administrator, value, source_fence, *, local_node):
    original, proposed, fingerprint = validate(value)
    p, role, placement = (proposed[k] for k in ('principal', 'role', 'placement'))
    local_node = str(UUID(local_node))
    required = {'version': 1, 'sourceNodeId': value['sourceNodeId'],
                'targetNodeId': local_node, 'planDigest': fingerprint,
                'principalId': p['id'], 'homeNodeId': local_node,
                'homeRevision': p['home_revision'], 'referenceOnly': True,
                'activeLocalCredential': False, 'workerCreated': False}
    if (local_node != value['targetNodeId']
            or any(source_fence.get(k) != v for k, v in required.items())):
        raise Denied('Exact independently read-back source fence required')
    with store.pool.connection() as c:
        actor = store.actor(c, administrator)
        store.admin(actor)
        if str(actor['authority']) != p['authority']:
            raise Denied('Organization differs')
        # Serialize even a first install where no principal row exists yet.
        c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (p['id'],))
        recorded = c.execute('SELECT * FROM hb_federation_home_receipt WHERE principal=%s',
                             (p['id'],)).fetchone()
        if recorded and recorded['plan_digest'] == fingerprint:
            # Return the original receipt. Do not resurrect a subsequently
            # revoked token, stopped placement or changed role.
            return recorded['receipt']
        old = c.execute('SELECT * FROM hb_principal WHERE id=%s FOR UPDATE', (p['id'],)).fetchone()
        if old:
            if (not old['identity_reference'] or str(old['authority']) != p['authority']
                    or str(old['home_node']) != value['sourceNodeId']
                    or old['home_revision'] != original['principal']['home_revision']):
                raise Conflict('Destination already has a different authoritative identity')
            live = c.execute("SELECT 1 FROM hb_executor WHERE agent=%s AND state!='stopped' LIMIT 1", (p['id'],)).fetchone()
            pending = c.execute("SELECT 1 FROM hb_delivery WHERE agent=%s AND state IN ('pending','claimed','running','cancel_requested','unknown') LIMIT 1", (p['id'],)).fetchone()
            if live or pending:
                raise Conflict('Destination reference is not drained')
        host = c.execute('SELECT 1 FROM hb_host WHERE id=%s AND authority=%s',
                         (placement['host'], p['authority'])).fetchone()
        if not host:
            raise Denied('Destination host must be independently enrolled')
        for field, table in (('home_room', 'hb_room'), ('decision_owner', 'hb_principal')):
            if role.get(field) is not None and not c.execute(
                    'SELECT 1 FROM '+table+' WHERE id=%s AND authority=%s',
                    (role[field], p['authority'])).fetchone():
                raise Denied('Role reference must already be explicitly enrolled: '+field)
        c.execute('''INSERT INTO hb_principal(id,authority,name,kind,administrator,active,home_node,home_revision,identity_reference)
            VALUES(%s,%s,%s,'agent',%s,true,%s,%s,false) ON CONFLICT(id) DO UPDATE
            SET name=EXCLUDED.name,administrator=EXCLUDED.administrator,active=true,
            home_node=EXCLUDED.home_node,home_revision=EXCLUDED.home_revision,identity_reference=false''',
            (p['id'], p['authority'], p['name'], p['administrator'], local_node, p['home_revision']))
        # Copy only the role schema. No event, native context, enrollment,
        # executor, host controller or model account is imported.
        columns = ('principal','authority','primary_role','home_room','purpose','revision','native_goal_scope','decision_owner')
        c.execute(sql.SQL('INSERT INTO hb_agent_role({}) VALUES({}) ON CONFLICT(principal) DO UPDATE SET {}').format(
            sql.SQL(',').join(map(sql.Identifier, columns)),
            sql.SQL(',').join(sql.Placeholder() for _ in columns),
            sql.SQL(',').join(sql.SQL('{}=EXCLUDED.{}').format(sql.Identifier(k),sql.Identifier(k)) for k in columns if k!='principal')),
            tuple(role[k] for k in columns))
        existing = c.execute('SELECT * FROM hb_placement WHERE agent=%s FOR UPDATE', (p['id'],)).fetchone()
        if existing and (str(existing['host']) != placement['host'] or existing['generation'] != placement['generation']):
            raise Conflict('Destination placement differs from original host generation')
        if not existing:
            c.execute('INSERT INTO hb_placement(authority,agent,host,enabled,generation) VALUES(%s,%s,%s,%s,%s)',
                      (p['authority'],p['id'],placement['host'],placement['enabled'],placement['generation']))
        elif not existing['enabled']:
            raise Conflict('Destination placement deliberately disabled')
        old_token = c.execute('SELECT * FROM hb_token WHERE digest=%s FOR UPDATE', (proposed['credentialDigest'],)).fetchone()
        if old_token and (str(old_token['principal']) != p['id'] or not old or not old['identity_reference']):
            raise Conflict('Credential is already enrolled outside this transfer')
        c.execute('INSERT INTO hb_token(digest,principal,revoked) VALUES(%s,%s,false) ON CONFLICT(digest) DO UPDATE SET revoked=false',
                  (proposed['credentialDigest'],p['id']))
        c.execute('UPDATE hb_federated_agent_grant SET enabled=false,revision=revision+1 WHERE principal=%s AND enabled', (p['id'],))
        receipt = {'version':1,'principalId':p['id'],'homeNodeId':local_node,
                   'homeRevision':p['home_revision'],'planDigest':fingerprint,
                   'sourceFenced':True,'nativeOrWorkerStarted':False,
                   'contextOrAccountCopied':False}
        from psycopg.types.json import Jsonb
        c.execute('''INSERT INTO hb_federation_home_receipt(principal,plan_digest,receipt) VALUES(%s,%s,%s)
            ON CONFLICT(principal) DO UPDATE SET plan_digest=EXCLUDED.plan_digest,receipt=EXCLUDED.receipt''',
            (p['id'],fingerprint,Jsonb(receipt)))
        return json_value(receipt)
