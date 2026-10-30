"""Explicit agent home ownership and room-scoped references within one organization.

A reference has no active local login. Its native effects require the original
home node's authenticated transport plus the receiving room's current grants.
This does not import Codex profiles, OAuth state, native history or a second worker.
"""
from dataclasses import dataclass
from uuid import UUID
import hmac

from .store import Denied, Conflict, json_value


@dataclass(frozen=True)
class ReferencedAgent:
    authority_id: str
    host_id: str
    principal_id: str
    token_digest: str
    home_node: str
    home_revision: int
    placement_generation: int
    role_revision: int
    grant_revision: int


def resolve_in_transaction(c, *, authority_id, source_host, principal_id, home_node, credential_digest):
    authority_id, source_host, principal_id, home_node = map(str,map(UUID,
        (authority_id,source_host,principal_id,home_node)))
    row=c.execute('''SELECT g.*,p.active,p.kind,p.home_node AS actual_home,
        p.home_revision AS actual_revision,p.identity_reference
        FROM hb_federated_agent_grant g JOIN hb_principal p ON p.id=g.principal AND p.authority=g.authority
        JOIN hb_placement pl ON pl.agent=p.id AND pl.authority=p.authority
            AND pl.host=g.source_host AND pl.generation=g.placement_generation
        JOIN hb_agent_role r ON r.principal=p.id AND r.authority=p.authority AND r.revision=g.role_revision
        WHERE g.authority=%s AND g.principal=%s AND g.home_node=%s AND g.source_host=%s
            AND g.enabled AND p.active AND p.kind='agent' AND p.identity_reference AND pl.enabled
        FOR SHARE OF g,p,pl,r''',(authority_id,principal_id,home_node,source_host)).fetchone()
    if (not row or str(row['actual_home'])!=home_node or row['actual_revision']!=row['home_revision']
            or not isinstance(credential_digest,str) or not hmac.compare_digest(row['credential_digest'],credential_digest)):
        raise Denied('Original home-node identity or receiving grant changed')
    return ReferencedAgent(authority_id,source_host,principal_id,credential_digest,home_node,
        row['home_revision'],row['placement_generation'],row['role_revision'],row['revision'])


def validate_admission(c, admission, actor_id, credential_digest):
    if admission.principal_id!=str(actor_id) or admission.token_digest!=credential_digest:
        raise Denied('Reference admission identity differs')
    current=resolve_in_transaction(c,authority_id=admission.authority_id,source_host=admission.host_id,
        principal_id=admission.principal_id,home_node=admission.home_node,credential_digest=credential_digest)
    if current!=admission:raise Denied('Reference grant changed during admission')


def describe(store, administrator, principal):
    """Private enrollment metadata only; no bearer/account credential is exported."""
    with store.pool.connection() as c:
        actor=store.actor(c,administrator);store.admin(actor)
        row=c.execute("SELECT * FROM hb_principal WHERE id=%s AND authority=%s AND kind='agent' AND active AND NOT identity_reference FOR SHARE",
            (principal,actor['authority'])).fetchone()
        if not row:raise Denied('Only an authoritative active agent may be described')
        credentials=c.execute('SELECT digest FROM hb_token WHERE principal=%s AND NOT revoked FOR SHARE',(principal,)).fetchall()
        if len(credentials)!=1:raise Conflict('Original credential must be unambiguous')
        role=c.execute('SELECT * FROM hb_agent_role WHERE principal=%s FOR SHARE',(principal,)).fetchone()
        placement=c.execute('SELECT * FROM hb_placement WHERE agent=%s AND enabled FOR SHARE',(principal,)).fetchone()
        if not role or not placement or row['home_node'] is None:raise Denied('Explicit home/role/placement required')
        return json_value({'version':1,'principal':row,'role':role,'placement':placement,
            'credentialDigest':credentials[0]['digest']})


def install_reference(store, administrator, descriptor, *, expected_local_home_revision=None,
                      expected_grant_revision=None, enroll_execution_reference=False,
                      expected_source_descriptor=None):
    """Installer operation; does not join rooms, place a new worker or copy auth."""
    if set(descriptor)!={'version','principal','role','placement','credentialDigest'} or descriptor['version']!=1:
        raise ValueError('Invalid home identity descriptor')
    p,r,pl=descriptor['principal'],descriptor['role'],descriptor['placement']
    principal,home,authority,host=map(str,map(UUID,(p['id'],p['home_node'],p['authority'],pl['host'])))
    if p['kind']!='agent' or p['identity_reference'] or not p['active'] or not pl['enabled']:
        raise Denied('Descriptor is not an authoritative placed agent')
    with store.pool.connection() as c:
        actor=store.actor(c,administrator);store.admin(actor)
        if str(actor['authority'])!=authority:raise Denied('Organization enrollment differs')
        old=c.execute('SELECT * FROM hb_principal WHERE id=%s FOR UPDATE',(principal,)).fetchone()
        if old and not old['identity_reference']:
            if expected_local_home_revision!=old['home_revision']:
                raise Conflict('Existing local ownership requires explicit reviewed transfer')
            if expected_source_descriptor is not None:
                current_role=c.execute('SELECT * FROM hb_agent_role WHERE principal=%s FOR SHARE',(principal,)).fetchone()
                current_placement=c.execute('SELECT * FROM hb_placement WHERE agent=%s FOR SHARE',(principal,)).fetchone()
                current_credentials=c.execute('SELECT digest FROM hb_token WHERE principal=%s AND NOT revoked FOR SHARE',(principal,)).fetchall()
                actual=json_value({'version':1,'principal':old,'role':current_role,'placement':current_placement,
                    'credentialDigest':current_credentials[0]['digest'] if len(current_credentials)==1 else None})
                if actual!=expected_source_descriptor:
                    raise Conflict('Original home state changed before fencing')
            live=c.execute("SELECT 1 FROM hb_executor WHERE agent=%s AND state!='stopped' LIMIT 1",(principal,)).fetchone()
            pending=c.execute("SELECT 1 FROM hb_delivery WHERE agent=%s AND state IN ('pending','claimed','running','cancel_requested','unknown') LIMIT 1",(principal,)).fetchone()
            if live or pending:raise Conflict('Local identity is not drained')
            active=c.execute('SELECT digest FROM hb_token WHERE principal=%s AND NOT revoked FOR SHARE',(principal,)).fetchall()
            if len(active)!=1 or active[0]['digest']!=descriptor['credentialDigest'] or p['home_revision']<=old['home_revision']:
                raise Conflict('Transfer must retain the original credential and advance home revision')
        if old and str(old['authority'])!=authority:raise Conflict('Global identity belongs to another organization')
        c.execute('''INSERT INTO hb_principal(id,authority,name,kind,administrator,active,home_node,home_revision,identity_reference)
            VALUES(%s,%s,%s,'agent',false,true,%s,%s,true)
            ON CONFLICT(id) DO UPDATE SET home_node=EXCLUDED.home_node,home_revision=EXCLUDED.home_revision,
                identity_reference=true,administrator=false''',(principal,authority,p['name'],home,p['home_revision']))
        # Old accepted histories and native context pointers remain. The old
        # credential becomes unusable even by a pre-federation HTTP server.
        c.execute('UPDATE hb_token SET revoked=true WHERE principal=%s',(principal,))
        c.execute('''INSERT INTO hb_agent_role(principal,authority,primary_role,purpose,revision)
            VALUES(%s,%s,%s,%s,%s) ON CONFLICT(principal) DO UPDATE SET
            primary_role=EXCLUDED.primary_role,purpose=EXCLUDED.purpose,revision=EXCLUDED.revision''',
            (principal,authority,r['primary_role'],r['purpose'],r['revision']))
        # Host enrollment is an independent, explicit prerequisite. Never
        # invent a host, execution placement, or machine identity from a message.
        existing=c.execute('SELECT * FROM hb_placement WHERE agent=%s AND authority=%s FOR SHARE',(principal,authority)).fetchone()
        if existing is None and enroll_execution_reference:
            registered=c.execute('SELECT 1 FROM hb_host WHERE id=%s AND authority=%s FOR SHARE',(host,authority)).fetchone()
            if not registered:raise Denied('Receiving host must already be explicitly enrolled')
            c.execute('INSERT INTO hb_placement(authority,agent,host,generation) VALUES(%s,%s,%s,%s)',
                (authority,principal,host,pl['generation']))
            existing={'host':host,'generation':pl['generation'],'enabled':True}
        if not existing or str(existing['host'])!=host or existing['generation']!=pl['generation'] or not existing['enabled']:
            raise Denied('Original explicit receiving execution placement required')
        previous=c.execute('SELECT * FROM hb_federated_agent_grant WHERE principal=%s FOR UPDATE',(principal,)).fetchone()
        unchanged=previous and all((str(previous['home_node'])==home,str(previous['source_host'])==host,
            previous['credential_digest']==descriptor['credentialDigest'],previous['home_revision']==p['home_revision'],
            previous['placement_generation']==pl['generation'],previous['role_revision']==r['revision'],previous['enabled']))
        if unchanged:
            return {'principalId':principal,'homeNodeId':home,'referenceOnly':True,'grantRevision':previous['revision'],
                'activeLocalCredential':False,'workerCreated':False}
        if previous and expected_grant_revision!=previous['revision']:
            raise Conflict('Reference grant replacement requires its original revision')
        row=c.execute('''INSERT INTO hb_federated_agent_grant(authority,principal,home_node,source_host,
            credential_digest,home_revision,placement_generation,role_revision,created_by)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(principal) DO UPDATE SET
            home_node=EXCLUDED.home_node,source_host=EXCLUDED.source_host,credential_digest=EXCLUDED.credential_digest,
            home_revision=EXCLUDED.home_revision,placement_generation=EXCLUDED.placement_generation,
            role_revision=EXCLUDED.role_revision,revision=hb_federated_agent_grant.revision+1,enabled=true,
            updated_at=clock_timestamp() RETURNING revision''',
            (authority,principal,home,host,descriptor['credentialDigest'],p['home_revision'],pl['generation'],r['revision'],actor['id'])).fetchone()
        return {'principalId':principal,'homeNodeId':home,'referenceOnly':True,'grantRevision':row['revision'],
            'activeLocalCredential':False,'workerCreated':False}


def install_room_reference(store, administrator, room, *, owner_node):
    """Foreign-key/route metadata only; never a second chat or membership."""
    owner_node=str(UUID(owner_node))
    required={'id','authority','name','purpose','owner','revision'}
    if not isinstance(room,dict) or not required<=set(room):
        raise ValueError('Original room metadata required')
    with store.pool.connection() as c:
        actor=store.actor(c,administrator);store.admin(actor)
        if str(actor['authority'])!=str(room['authority']):
            raise Denied('Organization differs')
        existing=c.execute('SELECT * FROM hb_room WHERE id=%s FOR UPDATE',(room['id'],)).fetchone()
        if existing:
            if (not existing['room_reference'] or str(existing['home_node'])!=owner_node
                    or any(json_value(existing[k])!=room[k] for k in required)):
                raise Conflict('Original room reference changed')
            return {'roomId':str(existing['id']),'ownerNodeId':owner_node,'referenceOnly':True}
        if not c.execute('SELECT 1 FROM hb_principal WHERE id=%s AND authority=%s',
                         (room['owner'],room['authority'])).fetchone():
            raise Denied('Original room owner must already be explicitly referenced')
        c.execute('''INSERT INTO hb_room(id,authority,name,purpose,owner,revision,home_node,room_reference)
            VALUES(%s,%s,%s,%s,%s,%s,%s,true)''',
            tuple(room[k] for k in ('id','authority','name','purpose','owner','revision'))+(owner_node,))
        return {'roomId':str(room['id']),'ownerNodeId':owner_node,'referenceOnly':True}
