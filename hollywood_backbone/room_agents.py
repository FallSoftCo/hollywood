"""Room-origin initialization; local runtimes still own native execution.

Reserve one deterministic identity per explicit environment. No credentials,
host processes, model calls, message replay or Global revocation on allocation.
"""
from uuid import UUID, uuid5
from psycopg.types.json import Jsonb
from .store import Conflict, Denied, json_value


def identity(room, node):
    return str(uuid5(UUID(str(room)), 'room-agent:' + str(node)))


def allocate(c, authority, room, *, new_project=False):
    # Existing default registrations are the explicit enrolled environments,
    # not a machine-name heuristic or every remembered offline host in a cache.
    rows = c.execute("""SELECT d.node,d.principal FROM hb_room_default_agent d
        JOIN hb_principal p ON p.id=d.principal AND p.authority=d.authority
        WHERE d.authority=%s AND d.enabled ORDER BY d.node""", (authority,)).fetchall()
    # Typed creation knows that there is no prior project fleet to hand off.
    # Explicit initialization of a retained room is ALWAYS staged instead.
    target=c.execute('SELECT id,room_kind FROM hb_room WHERE id=%s FOR UPDATE', (room,)).fetchone()
    if target['room_kind']=='global':raise Denied('Typed Global bootstrap is separate from project initialization')
    c.execute('INSERT INTO hb_room_agent_generation(authority,room,phase) VALUES(%s,%s,%s) ON CONFLICT(room) DO NOTHING',
              (authority, room, 'active' if new_project else 'staging'))
    for row in rows:
        c.execute("""INSERT INTO hb_room_agent_initialization(authority,room,node,agent,coordinator)
            VALUES(%s,%s,%s,%s,%s) ON CONFLICT(room,node) DO NOTHING""",
            (authority, room, row['node'], identity(room, row['node']), row['principal']))
    return len(rows)


def project(c, authority, rooms):
    """ONE batched setup-only projection for the caller's already-visible rooms.

    Never scans history, NATS, or each room in a loop. Native/business activity
    is separate. observedAt belongs to this computed response, not task end.
    """
    if not rooms:return {}
    rows=c.execute("""SELECT r.id AS projected_room_id,r.room_kind,
        g.phase AS generation_phase,g.revision AS generation_revision,g.handoff AS generation_handoff,
        floor(extract(epoch FROM statement_timestamp())*1000)::bigint AS projection_observed_at,
        i.*,h.name AS host_name,p.active AS identity_active,
        o.room AS origin_room,o.node AS origin_node,
        (m.can_read AND m.can_post AND m.can_request) AS member,
        n.state AS native_status,n.native_thread,
        n.observed_at > statement_timestamp()-interval '75 seconds' AS native_fresh,
        d.enabled AS coordinator_enabled,d.principal AS current_coordinator
        FROM hb_room r LEFT JOIN hb_room_agent_generation g ON g.room=r.id
        LEFT JOIN hb_room_agent_initialization i ON i.room=r.id AND i.authority=r.authority
        LEFT JOIN hb_host h ON h.authority=i.authority AND h.id=i.node
        LEFT JOIN hb_principal p ON p.id=i.agent
        LEFT JOIN hb_room_agent_origin o ON o.agent=i.agent
        LEFT JOIN hb_membership m ON m.room=i.room AND m.principal=i.agent
        LEFT JOIN hb_conversation_state n ON n.room=i.room AND n.agent=i.agent
          AND n.native_thread=i.receipt->>'nativeThreadId'
        LEFT JOIN hb_room_default_agent d ON d.authority=i.authority AND d.node=i.node
        WHERE r.authority=%s AND r.id=ANY(%s::uuid[]) ORDER BY r.id,i.node""",(authority,rooms)).fetchall()
    result={}
    for row in rows:
        room=str(row.pop('projected_room_id'));kind=row.pop('room_kind')
        phase=row.pop('generation_phase');revision=row.pop('generation_revision');handoff=row.pop('generation_handoff')
        observed=row.pop('projection_observed_at')
        required=kind!='global' and phase is not None
        value=result.setdefault(room,{'version':1,'model':'room-origin-per-environment' if required else
            'global-control-plane' if kind=='global' else 'legacy-participants',
            'roomId':room,'roomKind':kind,'observedAt':observed,'initializationRequired':required,
            'generation':{'phase':phase,'revision':revision,'handoff':handoff} if phase else None,
            'agents':[],'capabilities':{'controlStatus':1},
            'meaning':'Setup/native attachment only; business completion is never inferred. Historical sources and recipient snapshots are retained.'})
        if row['agent'] is None:continue
        row['ready']=bool(row['identity_active'] and row['member'] and row['native_fresh']
            and row['native_status']['type'] in ('idle','active','activeElsewhere')
            and row['receipt'] and row['receipt'].get('sharedAppServer') is True
            and str(row['origin_room'])==room and str(row['origin_node'])==str(row['node']))
        if row['ready']:row['readiness']='ready'
        elif (row.get('control_status') or {}).get('state') in ('blocked','unknown'):
            row['readiness']=row['control_status']['state'];row['note']=row['control_status']['note']
        elif not row['coordinator_enabled'] or row['current_coordinator']!=row['coordinator']:
            row['readiness']='blocked';row['note']='Original environment initializer is disabled/replaced; reviewed owner recovery required'
        elif row['state'] in ('blocked','unknown'):row['readiness']=row['state']
        elif not row['host_name']:row['readiness']='host-descriptor-unavailable'
        elif row['receipt']:row['readiness']='awaiting-fresh-native-observation'
        else:row['readiness']=row['state']
        row.pop('native_status',None) # no another source's private busy/wait flags
        value['agents'].append(row)
    for value in result.values():
        agents=value['agents'];value['desiredCount']=len(agents);value['readyCount']=sum(bool(a['ready']) for a in agents)
        value['nativeReady']=bool(agents) and value['readyCount']==len(agents)
        value['complete']=bool(value['initializationRequired'] and value['nativeReady'] and value['generation']['phase']=='active')
        if not value['initializationRequired']:
            value['setupState']='not-required';value['reason']='Global control-plane room' if value['roomKind']=='global' else 'Retained legacy participants; no room-origin initialization allocated'
        elif value['complete']:value['setupState']='ready';value['reason']='All desired environments have fresh matching native observations'
        elif value['nativeReady']:value['setupState']='handoff-required';value['reason']='Staged agents native-ready; original-work handoff has not been activated'
        elif any(a['readiness'] in ('blocked','unknown') for a in agents):
            value['setupState']='blocked';value['reason']='One or more environment prerequisites/original receipts require recovery'
        else:
            value['setupState']='pending';value['reason']='Awaiting registered environments' if not agents else 'Environment initialization or fresh native observations pending'
    return json_value(result)


def inventory(c, authority, room):
    return project(c,authority,[room])[str(room)]


def controlled_slots(c, actor, room=None):
    """Explicit registered home coordinator metadata, NOT room read/admin rights.

    An authenticated foreign home reference correctly loses administrator=true.
    Do not require restoring that broad bit to discover its own desired slots.
    Existing transport admission still fences its exact home/credential grant.
    """
    if actor['kind']!='agent':return []
    query="""SELECT i.* FROM hb_room_agent_initialization i
        JOIN hb_room_default_agent d ON d.authority=i.authority AND d.node=i.node
          AND d.principal=i.coordinator AND d.enabled
        WHERE i.authority=%s AND i.coordinator=%s AND i.node=%s"""
    args=[actor['authority'],actor['id'],actor['home_node']]
    if room is not None:query+=' AND i.room=%s';args.append(room)
    return c.execute(query+' ORDER BY i.room,i.node',args).fetchall()


def read(store, actor_id, room):
    with store.pool.connection() as c:
        actor = store.actor(c, actor_id)
        slots=controlled_slots(c,actor,room)
        if slots:
            result=inventory(c,actor['authority'],room)
            result['agents']=[r for r in result['agents'] if r['coordinator']==str(actor['id'])]
            result.update(controlOnly=True,complete=False,nativeReady=False)
            return result
        store.room(c, actor, room)
        return inventory(c, actor['authority'], room)


def initialize(store, c, actor, room):
    if not actor['administrator']:
        if not controlled_slots(c,actor,room):raise Denied('Original registered host slot required')
        result=inventory(c,actor['authority'],room)
        result['agents']=[r for r in result['agents'] if r['coordinator']==str(actor['id'])]
        result.update(controlOnly=True,complete=False,nativeReady=False)
        return result # Already allocated canonical intent; no fresh fleet allocation.
    store.room(c, actor, room)
    allocate(c, actor['authority'], room)
    return inventory(c, actor['authority'], room)


def enroll(store, c, actor, room, node, payload):
    store.room(c, actor, room)
    slot = c.execute('SELECT * FROM hb_room_agent_initialization WHERE room=%s AND node=%s FOR UPDATE',
                     (room, node)).fetchone()
    if not slot or str(slot['coordinator']) != str(actor['id']) or str(actor.get('home_node')) != node:
        raise Denied('Only the enrolled environment owner can initialize its original slot')
    current = c.execute('SELECT principal,enabled FROM hb_room_default_agent WHERE authority=%s AND node=%s FOR SHARE', (actor['authority'],node)).fetchone()
    if not current or not current['enabled'] or current['principal'] != slot['coordinator']:
        raise Denied('Original environment initialization authority was revoked or replaced')
    # Remote homes require the existing authoritative-home/reference enrollment
    # path. Never forge a remote home or put a remotely-owned token at this node.
    if getattr(store, 'local_node_id', None) != node:
        raise Conflict('Home-node enrollment/reference adapter required; original slot remains pending')
    agent = str(slot['agent'])
    descriptor = c.execute('SELECT name FROM hb_host WHERE id=%s AND authority=%s',
                           (node, actor['authority'])).fetchone()
    if not descriptor:
        raise Denied('Actual enrolled host descriptor required')
    room_name = c.execute('SELECT name FROM hb_room WHERE id=%s', (room,)).fetchone()['name']
    existing = c.execute('SELECT home_node,identity_reference FROM hb_principal WHERE id=%s', (agent,)).fetchone()
    if existing and (existing['identity_reference'] or existing['home_node'] is not None and str(existing['home_node']) != node):
        raise Conflict('Original reserved identity has a different home; no replacement allowed')
    name = room_name[:70] + ' · ' + descriptor['name'][:70] + ' · ' + agent[-8:]
    result = store._admin_principal(c, actor, agent, name, 'agent',
                                   payload['enrollmentCredential'])
    origin = c.execute('SELECT * FROM hb_room_agent_origin WHERE agent=%s', (agent,)).fetchone()
    if origin and (str(origin['room']) != room or str(origin['node']) != node):
        raise Conflict('Immutable room origin differs')
    if not origin:
        if not result['created']:
            raise Conflict('Existing agent needs reviewed origin adoption; never rewrite its role/context')
        c.execute('INSERT INTO hb_room_agent_origin VALUES(%s,%s,%s,%s)',
                  (agent, actor['authority'], room, node))
        c.execute('UPDATE hb_principal SET home_node=%s WHERE id=%s', (node, agent))
        c.execute("""UPDATE hb_agent_role SET home_room=%s,primary_role='room-agent',
            purpose=%s WHERE principal=%s""",
            (room, 'Own this room on the enrolled environment; preserve independent source/task scope', agent))
    placement=c.execute('SELECT * FROM hb_placement WHERE agent=%s AND authority=%s AND enabled FOR SHARE',
                        (actor['id'],actor['authority'])).fetchone()
    if not placement or str(placement['host'])!=node:raise Denied('Original home host placement required')
    c.execute('INSERT INTO hb_placement(authority,agent,host) VALUES(%s,%s,%s) ON CONFLICT(agent) DO NOTHING',
              (actor['authority'],agent,node))
    child=c.execute('SELECT * FROM hb_placement WHERE agent=%s',(agent,)).fetchone()
    if not child['enabled'] or str(child['host'])!=node:raise Conflict('Original child placement changed')
    if not c.execute("SELECT to_regclass('hb_backplane_agent_binding') AS name").fetchone()['name']:
        raise Denied('Existing home transport enrollment schema must already be installed')
    from .store import digest
    c.execute("""INSERT INTO hb_backplane_agent_binding(authority,host,principal,token_digest,
        placement_generation,created_by,updated_by) VALUES(%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT(authority,host,principal) DO NOTHING""",
        (actor['authority'],node,agent,digest(payload['enrollmentCredential']),child['generation'],actor['id'],actor['id']))
    binding=c.execute('SELECT * FROM hb_backplane_agent_binding WHERE authority=%s AND host=%s AND principal=%s',
                      (actor['authority'],node,agent)).fetchone()
    if not binding['enabled'] or binding['token_digest']!=digest(payload['enrollmentCredential']) or binding['placement_generation']!=child['generation']:
        raise Conflict('Original home transport binding changed/revoked; no repair')
    c.execute("""INSERT INTO hb_membership(authority,room,principal,can_read,can_post,can_request)
        VALUES(%s,%s,%s,true,true,true) ON CONFLICT(room,principal) DO NOTHING""", (actor['authority'], room, agent))
    c.execute("""UPDATE hb_room_agent_initialization SET state='enrolled',revision=revision+1,
        note='Identity and origin accepted; native provisioning not yet verified' WHERE room=%s AND node=%s AND state='pending'""", (room, node))
    return {'agentId': agent, 'originRoomId': room, 'homeNodeId': node,
            'enrollment': result, 'nativeProvisioned': False}


def report(store, c, actor, room, node, payload):
    slot = c.execute('SELECT * FROM hb_room_agent_initialization WHERE room=%s AND node=%s FOR UPDATE', (room, node)).fetchone()
    if not slot or not any(str(r['node'])==node for r in controlled_slots(c,actor,room)):
        raise Denied('Original enabled host-local initializer required')
    if payload['revision'] != slot['revision']:
        raise Conflict('Original initialization revision changed')
    if payload['state'] not in ('provisioning','blocked','unknown'):
        raise ValueError('Host reports provisioning evidence, never invents canonical Ready')
    receipt = payload.get('receipt')
    if receipt is not None:
        expected = {'nativeThreadId','nativeHostId','appServerManifestSha256','sharedAppServer'}
        if set(receipt) != expected or receipt['sharedAppServer'] is not True or receipt['nativeHostId'] != node:
            raise ValueError('Exact existing shared-native attachment evidence required')
        if not isinstance(receipt['nativeThreadId'], str) or not 1 <= len(receipt['nativeThreadId']) <= 128:
            raise ValueError('Original distinct native thread required')
        import re
        if not re.fullmatch('[0-9a-f]{64}', receipt['appServerManifestSha256']):
            raise ValueError('Pinned existing app-server manifest required')
        duplicate = c.execute("""SELECT 1 FROM hb_conversation_state WHERE native_thread=%s
            AND agent<>%s UNION ALL SELECT 1 FROM hb_room_agent_initialization
            WHERE receipt->>'nativeThreadId'=%s AND agent<>%s LIMIT 1""",
            (receipt['nativeThreadId'], slot['agent'], receipt['nativeThreadId'], slot['agent'])).fetchone()
        if duplicate:raise Conflict('Native thread already belongs to another agent; no context move allowed')
    c.execute('UPDATE hb_room_agent_initialization SET state=%s,note=%s,receipt=%s,revision=revision+1 WHERE room=%s AND node=%s',
              (payload['state'], payload['note'], Jsonb(payload.get('receipt')), room, node))
    result=inventory(c,actor['authority'],room)
    if not actor['administrator']:
        result['agents']=[r for r in result['agents'] if r['coordinator']==str(actor['id'])]
        result.update(controlOnly=True,complete=False,nativeReady=False)
    return result


def activate(store, c, actor, room, payload):
    """Explicit owner-reviewed communication handoff, not a native stop or replay."""
    store.room(c, actor, room)
    c.execute('SELECT id FROM hb_room WHERE id=%s FOR UPDATE', (room,))
    generation = c.execute('SELECT * FROM hb_room_agent_generation WHERE room=%s FOR UPDATE', (room,)).fetchone()
    if not generation or generation['phase']!='staging' or generation['revision']!=payload['revision']:
        raise Conflict('Original staged recipient generation changed')
    desired = inventory(c, actor['authority'], room)
    if not desired['nativeReady']:raise Conflict('Every desired environment requires fresh actual native readiness before handoff')
    original = c.execute("""SELECT p.id FROM hb_membership m JOIN hb_principal p ON p.id=m.principal
        WHERE m.room=%s AND m.can_read AND p.kind='agent'
        AND NOT EXISTS(SELECT 1 FROM hb_room_agent_initialization i WHERE i.room=m.room AND i.agent=m.principal)
        AND EXISTS(SELECT 1 FROM hb_room_agent_initialization i WHERE i.room=m.room AND i.coordinator=m.principal)
        ORDER BY p.id FOR UPDATE OF m""", (room,)).fetchall()
    retiring = sorted(str(r['id']) for r in original)
    if retiring != sorted(set(payload['retiringAgentIds'])):
        raise Conflict('Original public participant set changed; re-review handoff')
    # Proof is an explicit trusted owner attestation, not an API inference that
    # active Apple/browser/native work ended. No host lifecycle is performed here.
    proof = payload['handoff']
    if proof.get('originalWorkDisposition') != 'retained-with-original-owners' or not proof.get('ownerReceiptSha256'):
        raise ValueError('Reviewed original work/source/private-output retention receipt required')
    c.execute("UPDATE hb_room_agent_generation SET phase='active',revision=revision+1,retired=%s::uuid[],handoff=%s WHERE room=%s",
              (retiring, Jsonb(proof), room))
    c.execute("""UPDATE hb_membership SET can_post=false,can_request=false,revision=revision+1
        WHERE room=%s AND principal=ANY(%s::uuid[]) AND (can_post OR can_request)""", (room,retiring))
    # Original principals remain addressable and can reconcile their old work.
    # Retained read/source-owner access is not active project participation.
    # Future unaddressed PUBLIC fanout changes. Their explicit private
    # sources/queued outputs and historical send-time recipients are untouched.
    return inventory(c, actor['authority'], room)


def control_status(store, actor_id, room, node, payload):
    """Current own-host preparation observation; no admin/room-history promotion."""
    if (not isinstance(payload,dict) or set(payload)!={'revision','state','note','originalOperation'}
        or type(payload['revision']) is not int or not 1<=payload['revision']<9007199254740991
        or payload['state'] not in ('preparing','blocked','unknown')
        or not isinstance(payload['note'],str) or not 1<=len(payload['note'])<=1000
        or not isinstance(payload['originalOperation'],str) or not 1<=len(payload['originalOperation'])<=128):
        raise ValueError('Exact own-host preparation observation required; readiness cannot be reported')
    with store.pool.connection() as c:
        actor=store.actor(c,actor_id)
        slots=controlled_slots(c,actor,room)
        if not any(str(r['node'])==node for r in slots):
            raise Denied('Original enabled home coordinator alone may report this slot')
        row=c.execute('SELECT * FROM hb_room_agent_initialization WHERE room=%s AND node=%s FOR UPDATE',(room,node)).fetchone()
        if payload['revision']==row['control_revision']:
            if payload!=row['control_status']:raise Conflict('Original host preparation observation changed')
        elif payload['revision']==row['control_revision']+1:
            c.execute('UPDATE hb_room_agent_initialization SET control_status=%s,control_revision=%s,control_observed_at=clock_timestamp() WHERE room=%s AND node=%s',
                (Jsonb(payload),payload['revision'],room,node))
        else:raise Conflict('Original host preparation observation revision changed')
        return {'accepted':True,'authorityId':str(actor['authority']),'roomId':room,'nodeId':node,
                'coordinatorId':str(actor['id']),'revision':payload['revision'],'status':payload,
                'nativeReadiness':'not_inferred','businessCompletion':'not_inferred'}
