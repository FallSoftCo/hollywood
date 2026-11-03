"""Home-owned identity birth and canonical room reference adoption.

No native process/thread, copied profile or remote raw credential. The async
bridge MUST obtain Allocation from the authenticated existing node transport;
this module is not a public raw-JSON enrollment endpoint by itself.
"""
from dataclasses import dataclass
from uuid import UUID
import re
from psycopg.types.json import Jsonb
from .store import Conflict,Denied,digest,json_value
from .room_agents import controlled_slots,identity


@dataclass(frozen=True)
class Allocation:
    producer_node: str
    body: dict


def allocation_read(store,actor_id,room,node):
    with store.pool.connection() as c:
        actor=store.actor(c,actor_id)
        slots=controlled_slots(c,actor,room)
        slot=next((r for r in slots if str(r['node'])==node),None)
        if slot is None:raise Denied('Original enabled home coordinator allocation required')
        target=c.execute('SELECT * FROM hb_room WHERE authority=%s AND id=%s AND NOT room_reference',
                         (actor['authority'],room)).fetchone()
        if not target:raise Denied('Canonical room owner alone describes allocation')
        owner=getattr(store,'local_node_id',None)
        if owner is None or target['home_node'] is not None and str(target['home_node'])!=owner:
            raise Denied('Actual canonical room node is required')
        return json_value({'version':1,'ownerNodeId':owner,'room':target,
            'node':node,'agent':slot['agent'],'coordinator':slot['coordinator']})


def home_birth(store,actor_id,allocation,node,credential,operation):
    """PRIVATE bridge primitive: allocation comes from a verified node response.

    The receiving CPU never calls this with node=Mac/Windows. No reference or
    token is manufactured on the wrong home. No arbitrary principal is adopted.
    """
    if not isinstance(allocation,Allocation):raise Denied('Authenticated allocation response required, not caller JSON')
    proof=allocation.body
    if proof.get('version')!=1 or proof.get('ownerNodeId')!=allocation.producer_node:
        raise Denied('Allocation producer differs from authenticated room owner')
    node=str(UUID(node));room=str(UUID(proof['room']['id']));agent=identity(room,node)
    if (proof.get('node'),proof.get('agent'),proof.get('coordinator'))!=(node,agent,actor_id):
        raise Denied('Original room/home/coordinator allocation differs')
    if getattr(store,'local_node_id',None)!=node:
        raise Denied('Credential birth is permitted ONLY at its actual home node')
    if not isinstance(credential,str) or not 40<=len(credential)<=256:
        raise ValueError('Original saved home credential required')
    if not isinstance(operation,str) or not 1<=len(operation)<=128:raise ValueError('Original home operation required')
    fingerprint=digest({'allocation':proof,'node':node,'credentialDigest':digest(credential)})
    with store.pool.connection() as c:
        actor=store.actor(c,actor_id);store.admin(actor)
        if str(actor['home_node'])!=node or str(actor['authority'])!=proof['room']['authority']:
            raise Denied('Original home administrator/authority differs')
        c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',(actor_id+':room-home:'+operation,))
        prior=c.execute('SELECT * FROM hb_room_agent_home_birth WHERE coordinator=%s AND operation=%s',
                        (actor_id,operation)).fetchone()
        if prior:
            if prior['fingerprint']!=fingerprint:raise Conflict('Original home birth command changed')
            return prior['descriptor']
        target=c.execute('SELECT * FROM hb_room WHERE id=%s AND authority=%s FOR SHARE',(room,actor['authority'])).fetchone()
        if not target or any(json_value(target[k])!=proof['room'][k] for k in ('id','authority','name','purpose','owner','revision')):
            raise Denied('Original canonical room metadata/reference must be installed before home birth')
        actual_owner=str(target['home_node'] or store.local_node_id)
        if actual_owner!=allocation.producer_node:raise Denied('Original room reference owner differs')
        placement=c.execute('SELECT * FROM hb_placement WHERE agent=%s AND authority=%s AND enabled FOR SHARE',
                            (actor_id,actor['authority'])).fetchone()
        if not placement:raise Denied('Original home host placement must already be enrolled')
        host=c.execute('SELECT name FROM hb_host WHERE id=%s AND authority=%s FOR SHARE',
                       (placement['host'],actor['authority'])).fetchone()
        existing=c.execute('SELECT * FROM hb_principal WHERE id=%s FOR UPDATE',(agent,)).fetchone()
        if existing and (existing['identity_reference'] or str(existing['home_node'])!=node):
            raise Conflict('Reserved identity has a different home; no transfer or credential copy')
        name=target['name'][:70]+' · '+host['name'][:70]+' · '+agent[-8:]
        enrolled=store._admin_principal(c,actor,agent,name,'agent',credential)
        origin=c.execute('SELECT * FROM hb_room_agent_origin WHERE agent=%s',(agent,)).fetchone()
        if origin and (str(origin['room']),str(origin['node']))!=(room,node):raise Conflict('Immutable original room/home differs')
        if not origin:
            if not enrolled['created']:raise Conflict('Existing non-origin identity cannot be silently adopted')
            c.execute('INSERT INTO hb_room_agent_origin VALUES(%s,%s,%s,%s)',(agent,actor['authority'],room,node))
            c.execute('UPDATE hb_principal SET home_node=%s WHERE id=%s',(node,agent))
            c.execute("UPDATE hb_agent_role SET home_room=%s,primary_role='room-agent',purpose=%s WHERE principal=%s",
                      (room,'Own this origin room on its enrolled environment, preserving original source/task boundaries',agent))
        c.execute('INSERT INTO hb_placement(authority,agent,host) VALUES(%s,%s,%s) ON CONFLICT(agent) DO NOTHING',
                  (actor['authority'],agent,placement['host']))
        p=c.execute('SELECT * FROM hb_principal WHERE id=%s',(agent,)).fetchone()
        r=c.execute('SELECT * FROM hb_agent_role WHERE principal=%s',(agent,)).fetchone()
        pl=c.execute('SELECT * FROM hb_placement WHERE agent=%s',(agent,)).fetchone()
        if not pl['enabled'] or pl['host']!=placement['host']:raise Conflict('Original logical home placement changed')
        if not c.execute("SELECT to_regclass('hb_backplane_agent_binding') AS name").fetchone()['name']:
            raise Denied('Existing home transport enrollment schema must already be installed')
        c.execute('''INSERT INTO hb_backplane_agent_binding(authority,host,principal,token_digest,
            placement_generation,created_by,updated_by) VALUES(%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT(authority,host,principal) DO NOTHING''',
            (actor['authority'],pl['host'],agent,digest(credential),pl['generation'],actor_id,actor_id))
        binding=c.execute('SELECT * FROM hb_backplane_agent_binding WHERE authority=%s AND host=%s AND principal=%s',
                          (actor['authority'],pl['host'],agent)).fetchone()
        if not binding['enabled'] or binding['token_digest']!=digest(credential) or binding['placement_generation']!=pl['generation']:
            raise Conflict('Original home transport enrollment changed/revoked; no implicit repair')
        descriptor=json_value({'version':1,'identity':{'version':1,'principal':p,'role':r,'placement':pl,
            'credentialDigest':digest(credential)},'origin':{'agent':agent,'authority':actor['authority'],'room':room,'node':node}})
        c.execute('INSERT INTO hb_room_agent_home_birth VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',
                  (actor['authority'],actor_id,operation,room,node,agent,fingerprint,Jsonb(descriptor)))
        return descriptor


def attach_home(store,actor_id,room,node,operation,descriptor):
    """Canonical receiver adopts ONLY the home descriptor of its reserved slot.

    Existing AgentDomain admission authenticates the original home coordinator;
    this exact metadata scope does not restore administrator=true on a reference.
    """
    if not isinstance(descriptor,dict) or set(descriptor)!={'version','identity','origin'} or descriptor['version']!=1:
        raise ValueError('Exact home-origin descriptor required')
    d=descriptor['identity']
    if set(d)!={'version','principal','role','placement','credentialDigest'} or d['version']!=1:raise ValueError('Existing home identity descriptor required')
    p,r,pl=d['principal'],d['role'],d['placement'];agent=identity(room,node)
    if (p['id'],p['home_node'],p['kind'],p['active'],p['administrator'],p['identity_reference'])!=(agent,node,'agent',True,False,False):
        raise Denied('Only the original authoritative nonadmin room agent can be referenced')
    if (r['principal'],r['authority'],r['home_room'],r['primary_role'])!=(agent,p['authority'],room,'room-agent'):
        raise Denied('Original immutable room role differs')
    if descriptor['origin']!={'agent':agent,'authority':p['authority'],'room':room,'node':node}:
        raise Denied('Original canonical origin descriptor differs')
    if not re.fullmatch('[0-9a-f]{64}',d['credentialDigest']):raise ValueError('Only the home credential DIGEST is admitted')
    if type(p['home_revision']) is not int or p['home_revision']<1 or type(r['revision']) is not int or r['revision']<1:
        raise ValueError('Exact original home/role revisions required')
    if pl['agent']!=agent or pl['authority']!=p['authority'] or pl['enabled'] is not True or type(pl['generation']) is not int or pl['generation']<1:
        raise Denied('Original explicit home placement required')
    if not isinstance(operation,str) or not 1<=len(operation)<=128:raise ValueError('Original attachment operation required')
    fingerprint=digest({'room':room,'node':node,'descriptor':descriptor})
    with store.pool.connection() as c:
        actor=store.actor(c,actor_id)
        slots=controlled_slots(c,actor,room)
        if not any(str(i['node'])==node for i in slots):raise Denied('Original enabled home coordinator required')
        if str(actor['authority'])!=p['authority']:raise Denied('Origin authority differs')
        c.execute('SELECT * FROM hb_room_agent_initialization WHERE room=%s AND node=%s FOR UPDATE',(room,node)).fetchone()
        old=c.execute('SELECT * FROM hb_room_agent_home_attachment WHERE coordinator=%s AND operation=%s',
                      (actor_id,operation)).fetchone()
        if old:
            if old['fingerprint']!=fingerprint:raise Conflict('Original receiving attachment command changed')
            return old['accepted_result']
        target=c.execute('SELECT * FROM hb_room WHERE id=%s AND authority=%s AND NOT room_reference',
                         (room,actor['authority'])).fetchone()
        if not target or str(target['home_node'] or store.local_node_id)!=store.local_node_id:
            raise Denied('Canonical room owner alone adopts a home reference')
        if node==store.local_node_id:raise Denied('A local authoritative identity must not become a receiving reference')
        coordinator_placement=c.execute('SELECT * FROM hb_placement WHERE agent=%s AND enabled FOR SHARE',(actor_id,)).fetchone()
        if not coordinator_placement or str(coordinator_placement['host'])!=pl['host']:
            raise Denied('Descriptor host is not the original authenticated coordinator environment')
        if not c.execute('SELECT 1 FROM hb_host WHERE id=%s AND authority=%s',(pl['host'],actor['authority'])).fetchone():
            raise Denied('Original receiving host must already be explicitly enrolled')
        existing=c.execute('SELECT * FROM hb_principal WHERE id=%s FOR UPDATE',(agent,)).fetchone()
        if existing:
            if (not existing['active'] or not existing['identity_reference'] or str(existing['home_node'])!=node
                or existing['home_revision']!=p['home_revision'] or str(existing['authority'])!=p['authority']):
                raise Conflict('Reserved identity already has a different authoritative home; no takeover')
            grant=c.execute('SELECT * FROM hb_federated_agent_grant WHERE principal=%s',(agent,)).fetchone()
            role=c.execute('SELECT * FROM hb_agent_role WHERE principal=%s',(agent,)).fetchone()
            place=c.execute('SELECT * FROM hb_placement WHERE agent=%s',(agent,)).fetchone()
            origin=c.execute('SELECT * FROM hb_room_agent_origin WHERE agent=%s',(agent,)).fetchone()
            if (not grant or not grant['enabled'] or grant['credential_digest']!=d['credentialDigest']
                or grant['role_revision']!=r['revision'] or grant['home_revision']!=p['home_revision']
                or not role or str(role['home_room'])!=room or role['revision']!=r['revision']
                or not place or not place['enabled'] or str(place['host'])!=pl['host'] or place['generation']!=pl['generation']
                or not origin or (str(origin['room']),str(origin['node']))!=(room,node)):
                raise Conflict('Existing reference changed/revoked; no implicit grant repair')
        else:
            c.execute("""INSERT INTO hb_principal(id,authority,name,kind,administrator,active,home_node,home_revision,identity_reference)
                VALUES(%s,%s,%s,'agent',false,true,%s,%s,true)""",(agent,actor['authority'],p['name'],node,p['home_revision']))
            c.execute('INSERT INTO hb_agent_role(principal,authority,primary_role,home_room,purpose,revision) VALUES(%s,%s,%s,%s,%s,%s)',
                      (agent,actor['authority'],'room-agent',room,r['purpose'],r['revision']))
            c.execute('INSERT INTO hb_placement(authority,agent,host,generation) VALUES(%s,%s,%s,%s)',
                      (actor['authority'],agent,pl['host'],pl['generation']))
            c.execute('INSERT INTO hb_room_agent_origin VALUES(%s,%s,%s,%s)',(agent,actor['authority'],room,node))
            c.execute('''INSERT INTO hb_federated_agent_grant(authority,principal,home_node,source_host,credential_digest,
                home_revision,placement_generation,role_revision,created_by) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                (actor['authority'],agent,node,pl['host'],d['credentialDigest'],p['home_revision'],pl['generation'],r['revision'],actor_id))
        c.execute('INSERT INTO hb_membership(authority,room,principal,can_read,can_post,can_request) VALUES(%s,%s,%s,true,true,true) ON CONFLICT(room,principal) DO NOTHING',
                  (actor['authority'],room,agent))
        c.execute("UPDATE hb_room_agent_initialization SET state='enrolled',revision=revision+1,note='Original authoritative home descriptor accepted; no native readiness inferred' WHERE room=%s AND node=%s AND state='pending'",(room,node))
        result={'agentId':agent,'originRoomId':room,'homeNodeId':node,'referenceOnly':True,
                'activeLocalCredential':False,'nativeProvisioned':False,'workerCreated':False}
        c.execute('INSERT INTO hb_room_agent_home_attachment VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',
                  (actor['authority'],actor_id,operation,room,node,agent,fingerprint,Jsonb(result)))
        return result


def attachment_read(store,actor_id,room,node,operation):
    with store.pool.connection() as c:
        actor=store.actor(c,actor_id)
        if not any(str(r['node'])==node for r in controlled_slots(c,actor,room)):
            raise Denied('Original enabled coordinator alone reads its attachment')
        row=c.execute('SELECT accepted_result FROM hb_room_agent_home_attachment WHERE coordinator=%s AND operation=%s AND room=%s AND node=%s',
                      (actor_id,operation,room,node)).fetchone()
        if not row:raise Denied('Original receiving acceptance unavailable; absence does not authorize replay')
        return row['accepted_result']


def validate_home_descriptor(store,administrator,descriptor):
    """Fresh local proof before first outbound attach; old receipt != live grant."""
    from .federation_identity import describe
    original=descriptor['identity']
    if describe(store,administrator,original['principal']['id'])!=original:
        raise Conflict('Authoritative home identity changed after birth; preserve original operation')
    with store.pool.connection() as c:
        origin=c.execute('SELECT * FROM hb_room_agent_origin WHERE agent=%s',(original['principal']['id'],)).fetchone()
        if json_value(origin)!=descriptor['origin']:
            raise Conflict('Authoritative home origin changed')
    return descriptor
