"""Explicit reviewed legacy adoption; no guessed room kinds or rewritten birth.

Project adoption stages future recipients. Global adoption records a CURRENT
control-plane origin with its old role snapshot; it never rewrites home_room,
native history, accepted event sources, credentials or old delivery recipients.
"""
import re
from uuid import UUID
from psycopg.types.json import Jsonb
from .store import Conflict,Denied,json_value,digest


def review(payload,fields,optional=frozenset()):
    if not isinstance(payload,dict) or set(payload) not in (fields|{'reviewSha256'},fields|{'reviewSha256'}|optional) or not re.fullmatch('[0-9a-f]{64}',payload['reviewSha256']):
        raise ValueError('Exact reviewed ID/revision manifest required')
    for key in ('roomRevision','homeRevision','roleRevision'):
        if key in payload and (type(payload[key]) is not int or not 1<=payload[key]<=9007199254740991):
            raise ValueError('Exact integer original revision required')
    for key in ('roomId','ownerId','homeNodeId','priorHomeRoomId'):
        if key in payload and payload[key] is not None and (not isinstance(payload[key],str) or str(UUID(payload[key]))!=payload[key]):
            raise ValueError('Canonical original UUID required')


def room_adopt(store,c,actor,room,payload):
    store.admin(actor)
    review(payload,{'kind','roomRevision','ownerId','priorKind','environments','participantSnapshotSha256'},frozenset({'purpose','priorPurposeDigest'}))
    if payload['kind'] not in ('project','global'):raise ValueError('Explicit project/global kind required')
    target=c.execute('SELECT * FROM hb_room WHERE id=%s AND authority=%s AND NOT room_reference FOR UPDATE',
                      (room,actor['authority'])).fetchone()
    if not target or str(target['home_node'] or store.local_node_id)!=store.local_node_id:
        raise Denied('Original canonical room node alone adopts its kind')
    original=c.execute('SELECT * FROM hb_room_origin_adoption WHERE room=%s',(room,)).fetchone()
    if original:
        if original['manifest']!=payload:raise Conflict('Original room adoption manifest changed')
        return original['accepted_result']
    if (target['revision'],str(target['owner']),target['room_kind'])!=(payload['roomRevision'],payload['ownerId'],payload['priorKind']):
        raise Conflict('Original room revision/owner/kind changed')
    if payload['priorKind']!='legacy':raise Conflict('Typed rooms cannot be silently reclassified')
    if not isinstance(payload['environments'],list):raise ValueError('Explicit environment list required')
    expected=[{'node':str(r['node']),'coordinator':str(r['principal'])} for r in c.execute(
        'SELECT node,principal FROM hb_room_default_agent WHERE authority=%s AND enabled ORDER BY node',(actor['authority'],)).fetchall()]
    if sorted(payload['environments'],key=lambda r:r['node'])!=expected:
        raise Conflict('Exact currently enabled environment inventory required, including offline homes')
    if not expected:raise Denied('No enrolled environment inventory; do not adopt an empty fleet')
    from .room_participation import snapshot
    participants=snapshot(c,room)
    if payload['participantSnapshotSha256']!=participants['sha256']:
        raise Conflict('Every prior actor/membership revision requires a current reviewed snapshot')
    if payload['kind']=='project':
        from .room_agents import allocate
        allocate(c,actor['authority'],room) # STAGING, never changes current fan-out.
    else:
        if c.execute('SELECT 1 FROM hb_room_agent_generation WHERE room=%s',(room,)).fetchone():
            raise Conflict('Existing project generation cannot become a Global room')
    if payload['kind']=='global':
        globals=sorted(r['coordinator'] for r in expected)
        retired=sorted(r['agentId'] for r in participants['participants'] if r['agentId'] not in globals)
        cutoff=c.execute('SELECT next_seq FROM hb_authority WHERE id=%s',(actor['authority'],)).fetchone()['next_seq']
        c.execute('INSERT INTO hb_global_room_participation VALUES(%s,%s,%s::uuid[],%s::uuid[],%s,%s,%s)',
                  (actor['authority'],room,globals,retired,cutoff,Jsonb(participants['participants']),payload['reviewSha256']))
        for principal in globals:
            c.execute('INSERT INTO hb_membership(authority,room,principal,can_read,can_post,can_request) VALUES(%s,%s,%s,true,true,true) ON CONFLICT(room,principal) DO NOTHING',
                      (actor['authority'],room,principal))
        # Current/default recipients only; all existing grants/stops remain independent.
    purpose=target['purpose']
    if 'purpose' in payload:
        if not isinstance(payload['purpose'],str) or not 1<=len(payload['purpose'])<=4000 or payload['priorPurposeDigest']!=digest(target['purpose']):
            raise Conflict('Reviewed original purpose/prospective purpose changed')
        purpose=payload['purpose']
    c.execute('UPDATE hb_room SET room_kind=%s,purpose=%s,revision=revision+1 WHERE id=%s',(payload['kind'],purpose,room))
    result={'roomId':room,'roomKind':payload['kind'],'revision':target['revision']+1,
        'phase':'staging' if payload['kind']=='project' else 'global-control-plane',
        'originalRecipientsChanged':False,'nativeWorkReplayed':False}
    c.execute('INSERT INTO hb_room_origin_adoption(authority,room,actor,manifest,accepted_result,prior_room) VALUES(%s,%s,%s,%s,%s,%s)',
              (actor['authority'],room,actor['id'],Jsonb(payload),Jsonb(result),Jsonb(json_value(target))))
    return result


def global_adopt(store,c,actor,principal,payload):
    store.admin(actor)
    review(payload,{'roomId','roomRevision','ownerId','homeNodeId','homeRevision','roleRevision','priorHomeRoomId'})
    p=c.execute('SELECT * FROM hb_principal WHERE id=%s AND authority=%s AND active FOR UPDATE',
                (principal,actor['authority'])).fetchone()
    if not p or p['kind']!='agent' or p['identity_reference'] or str(p['home_node'])!=store.local_node_id or payload['homeNodeId']!=store.local_node_id:
        raise Denied('Original authoritative HOME alone adopts its existing Global')
    role=c.execute('SELECT * FROM hb_agent_role WHERE principal=%s FOR SHARE',(principal,)).fetchone()
    registration=c.execute('SELECT * FROM hb_placement WHERE authority=%s AND host=%s AND agent=%s AND enabled FOR SHARE',
                            (actor['authority'],store.local_node_id,principal)).fetchone()
    if not registration or not role or role['primary_role'].casefold()!='global':
        raise Denied('Explicit original enrolled Global role required, not a principal-name heuristic')
    old=c.execute('SELECT * FROM hb_global_origin WHERE agent=%s',(principal,)).fetchone()
    if old:
        if old['kind']!='reviewed-adoption' or old['manifest']!=payload:raise Conflict('Original Global origin differs')
        return json_value(old)
    if (p['home_revision'],role['revision'],str(role['home_room']) if role['home_room'] else None)!=(payload['homeRevision'],payload['roleRevision'],payload['priorHomeRoomId']):
        raise Conflict('Original Global home/role snapshot changed')
    target=c.execute('SELECT * FROM hb_room WHERE id=%s AND authority=%s FOR SHARE',(payload['roomId'],actor['authority'])).fetchone()
    if not target or (target['room_kind'],target['revision'],str(target['owner']))!=('global',payload['roomRevision'],payload['ownerId']):
        raise Denied('Explicit typed Global room/verified reference required')
    # No claim that this legacy agent was historically born in the chosen room.
    row=c.execute("""INSERT INTO hb_global_origin(authority,agent,room,node,kind,manifest,prior_role)
        VALUES(%s,%s,%s,%s,'reviewed-adoption',%s,%s) RETURNING *""",
        (actor['authority'],principal,payload['roomId'],store.local_node_id,Jsonb(payload),Jsonb(json_value(role)))).fetchone()
    return json_value(row)


def global_read(store,actor_id,principal):
    with store.pool.connection() as c:
        actor=store.actor(c,actor_id);store.admin(actor)
        p=c.execute('SELECT * FROM hb_principal WHERE id=%s AND authority=%s AND active AND NOT identity_reference',
                    (principal,actor['authority'])).fetchone()
        row=c.execute('SELECT * FROM hb_global_origin WHERE agent=%s',(principal,)).fetchone()
        if not p or str(p['home_node'])!=store.local_node_id or not row:
            raise Denied('Original authoritative Global origin unavailable')
        return json_value(row)


def global_attach(store,actor_id,operation,descriptor):
    """Existing AgentDomain home admission, not caller-provided admin authority."""
    if not isinstance(operation,str) or not 1<=len(operation)<=128:raise ValueError('Original receiving operation required')
    if not isinstance(descriptor,dict) or set(descriptor)!={'authority','agent','room','node','kind','manifest','prior_role'}:
        raise ValueError('Exact original home Global-origin descriptor required')
    with store.pool.connection() as c:
        actor=store.actor(c,actor_id)
        # A reference reaches this point ONLY under authenticated original
        # AgentDomain home admission; it cannot log in via local HTTP.
        if not actor['identity_reference'] or descriptor['agent']!=actor_id or descriptor['authority']!=str(actor['authority']) or descriptor['node']!=str(actor['home_node']):
            raise Denied('Original home-authenticated Global alone describes its origin')
        default=c.execute('SELECT 1 FROM hb_room_default_agent WHERE authority=%s AND node=%s AND principal=%s AND enabled',
                           (actor['authority'],actor['home_node'],actor_id)).fetchone()
        room=c.execute('SELECT * FROM hb_room WHERE id=%s AND authority=%s AND NOT room_reference FOR SHARE',
                        (descriptor['room'],actor['authority'])).fetchone()
        role=c.execute('SELECT * FROM hb_agent_role WHERE principal=%s FOR SHARE',(actor_id,)).fetchone()
        if not default or not room or room['room_kind']!='global' or str(room['home_node'] or store.local_node_id)!=store.local_node_id or not role or role['primary_role'].casefold()!='global':
            raise Denied('Current original Global and canonical typed origin room required')
        manifest=descriptor['manifest'];review(manifest,{'roomId','roomRevision','ownerId','homeNodeId','homeRevision','roleRevision','priorHomeRoomId'})
        if descriptor['kind']!='reviewed-adoption' or (manifest['roomId'],manifest['homeNodeId'],manifest['homeRevision'],manifest['roleRevision'])!=(str(room['id']),str(actor['home_node']),actor['home_revision'],role['revision']):
            raise Conflict('Current home origin/role revision differs')
        if (room['revision'],str(room['owner']))!=(manifest['roomRevision'],manifest['ownerId']):
            raise Conflict('Reviewed canonical Global room revision/owner changed')
        fingerprint=digest(descriptor)
        prior=c.execute('SELECT * FROM hb_global_origin_reference WHERE agent=%s AND operation=%s',(actor_id,operation)).fetchone()
        if prior:
            if prior['fingerprint']!=fingerprint:raise Conflict('Original Global reference operation changed')
            return prior['accepted_result']
        old=c.execute('SELECT * FROM hb_global_origin WHERE agent=%s FOR UPDATE',(actor_id,)).fetchone()
        if old and json_value(old)!=descriptor:raise Conflict('Existing declared Global origin differs; no rewrite')
        if not old:c.execute('INSERT INTO hb_global_origin VALUES(%s,%s,%s,%s,%s,%s,%s)',
            (descriptor['authority'],actor_id,descriptor['room'],descriptor['node'],descriptor['kind'],Jsonb(manifest),Jsonb(descriptor['prior_role'])))
        # Revocations remain authoritative; adoption does not overwrite false.
        c.execute('INSERT INTO hb_membership(authority,room,principal,can_read,can_post,can_request) VALUES(%s,%s,%s,true,true,true) ON CONFLICT(room,principal) DO NOTHING',
                  (actor['authority'],room['id'],actor_id))
        result={'agentId':actor_id,'originRoomId':str(room['id']),'originKind':'reviewed-global-adoption',
                'originalRoleUnchanged':True,'nativeWorkReplayed':False,'descriptorSha256':digest(descriptor)}
        c.execute('INSERT INTO hb_global_origin_reference VALUES(%s,%s,%s,%s,%s)',
                  (actor['authority'],actor_id,operation,fingerprint,Jsonb(result)))
        return result


def global_reference_read(store,actor_id,operation):
    with store.pool.connection() as c:
        actor=store.actor(c,actor_id)
        row=c.execute('SELECT accepted_result FROM hb_global_origin_reference WHERE authority=%s AND agent=%s AND operation=%s',
                       (actor['authority'],actor_id,operation)).fetchone()
        if not row:raise Denied('Original Global origin reference unavailable; no replay inferred')
        return row['accepted_result']


def refresh_global_reference(store,actor_id,producer,canonical,review_digest):
    """PRIVATE caller uses a verified NodeRouter response, never raw client JSON.

    Legacy references predate room_kind. Refresh only explicit Global metadata
    at its authenticated canonical owner, retaining the original row for audit.
    No membership, role, credential, history or context is changed.
    """
    if canonical.get('room_kind')!='global' or canonical.get('room_reference'):
        raise Denied('Authenticated canonical typed Global room required')
    if not isinstance(review_digest,str) or not re.fullmatch('[0-9a-f]{64}',review_digest):raise ValueError('Reviewed original metadata refresh required')
    if str(canonical.get('home_node') or producer)!=producer:raise Denied('Canonical producer differs')
    with store.pool.connection() as c:
        actor=store.actor(c,actor_id);store.admin(actor)
        old=c.execute('SELECT * FROM hb_room WHERE id=%s AND authority=%s FOR UPDATE',
                      (canonical['id'],actor['authority'])).fetchone()
        if not old:
            raise Denied('Original explicitly installed room reference required; no implicit owner enrollment')
        if not old['room_reference'] or str(old['home_node'])!=producer or str(old['owner'])!=canonical['owner'] or old['revision']>canonical['revision']:
            raise Conflict('Original room reference ownership/revision changed')
        if old['room_kind'] not in ('legacy','global'):raise Conflict('Project reference cannot become Global implicitly')
        if old['room_kind']=='global' and json_value(old)==canonical:return # canonical reference flag differs normally
        fp=digest({'room':canonical,'producer':producer,'reviewSha256':review_digest})
        c.execute('INSERT INTO hb_global_room_reference_refresh(authority,room,fingerprint,prior_room,canonical_room) VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                  (actor['authority'],canonical['id'],fp,Jsonb(json_value(old)),Jsonb(canonical)))
        c.execute("UPDATE hb_room SET room_kind='global',name=%s,purpose=%s,revision=%s WHERE id=%s",
                  (canonical['name'],canonical['purpose'],canonical['revision'],canonical['id']))
