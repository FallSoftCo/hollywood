"""Existing local HOME HTTP ingress -> authenticated canonical reference receipt.

The bearer is never forwarded to the canonical room node. Outbound dispatch is
CAS-journaled once. A restarted/unknown dispatch can only read that ORIGINAL
canonical acceptance; no node/operation substitution or blind resend.
"""
import asyncio
from psycopg.types.json import Jsonb
from .store import Conflict, Denied, digest, json_value
from .backplane import BackplaneError
from . import native_effects
from .room_agent_home import Allocation, home_birth, validate_home_descriptor
from .federation_identity import install_room_reference


def local(request,store,fn):
    """Revalidate original local credentials for each independent domain primitive."""
    with native_effects.transport(request['actor'],request['tokenDigest'],'POST',
            'room_agent_enroll',request.path.removeprefix('/hollywood/v3/'),None,None):
        return fn()


def get(store,actor_id,operation):
    with store.pool.connection() as c:
        actor=store.actor(c,actor_id);store.admin(actor)
        if actor['identity_reference'] or str(actor['home_node'])!=store.local_node_id:
            raise Denied('Original authoritative local home coordinator required')
        return c.execute('SELECT * FROM hb_room_agent_home_outbound WHERE authority=%s AND coordinator=%s AND operation=%s',
                         (actor['authority'],actor_id,operation)).fetchone()


def pin(store,actor_id,room,node,owner,payload,path,proof):
    with store.pool.connection() as c:
        actor=store.actor(c,actor_id);store.admin(actor)
        if actor['identity_reference'] or str(actor['home_node'])!=node or store.local_node_id!=node:
            raise Denied('Only actual local HOME may admit its enrollment')
        # Same namespace/lock as canonical administrative receipts. No original
        # accepted operation may be repurposed into a linked home enrollment.
        c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',(actor_id+':'+payload['operationId'],))
        if c.execute('SELECT 1 FROM hb_administrative_command WHERE principal=%s AND operation=%s',
                     (actor_id,payload['operationId'])).fetchone():raise Conflict('Original administrative operation already accepted elsewhere')
        c.execute("""INSERT INTO hb_room_agent_home_outbound
            (authority,coordinator,operation,room,node,owner_node,method,path,wire_digest,allocation,phase)
            VALUES(%s,%s,%s,%s,%s,%s,'POST',%s,%s,%s,'preparing') ON CONFLICT DO NOTHING""",
            (actor['authority'],actor_id,payload['operationId'],room,node,owner,path,
             digest({'method':'POST','path':path,'body':payload}),Jsonb(proof)))
    return get(store,actor_id,payload['operationId'])


async def prepare(router,request,row,credential):
    # Separate authenticated local primitives; no stale entered transport flag
    # is carried from the room-reference transaction into identity birth/CAS.
    store=router.store;actor_id=request['actor']
    await asyncio.to_thread(local,request,store,lambda:install_room_reference(store,actor_id,row['allocation']['room'],owner_node=str(row['owner_node'])))
    descriptor=await asyncio.to_thread(local,request,store,lambda:home_birth(store,actor_id,
        Allocation(str(row['owner_node']),row['allocation']),str(row['node']),credential,row['operation']))
    await asyncio.to_thread(local,request,store,lambda:validate_home_descriptor(store,actor_id,descriptor))
    def record():
        with store.pool.connection() as c:
            actor=store.actor(c,actor_id);store.admin(actor)
            c.execute("UPDATE hb_room_agent_home_outbound SET descriptor=%s,phase='prepared' WHERE coordinator=%s AND operation=%s AND phase='preparing'",
                      (Jsonb(descriptor),actor_id,row['operation']))
    await asyncio.to_thread(local,request,store,record)
    return await asyncio.to_thread(local,request,store,lambda:get(store,actor_id,row['operation']))


def dispatch_intent(store,actor_id,row):
    with store.pool.connection() as c:
        actor=store.actor(c,actor_id);store.admin(actor)
        return bool(c.execute("UPDATE hb_room_agent_home_outbound SET phase='dispatching' WHERE coordinator=%s AND operation=%s AND phase='prepared' RETURNING operation",
                               (actor_id,row['operation'])).fetchone())


def finish(store,actor_id,row,response):
    with store.pool.connection() as c:
        actor=store.actor(c,actor_id);store.admin(actor)
        c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',(actor_id+':'+row['operation'],))
        current=c.execute('SELECT * FROM hb_room_agent_home_outbound WHERE coordinator=%s AND operation=%s FOR UPDATE',
                           (actor_id,row['operation'])).fetchone()
        if current['phase'] in ('answered','rejected'):
            if current['response']!=response:raise Conflict('Original linked response changed')
            return current['response']
        if response['status']==200:
            expected={'agentId':row['descriptor']['identity']['principal']['id'],
                      'originRoomId':str(row['room']),'homeNodeId':str(row['node']),
                      'referenceOnly':True,'activeLocalCredential':False,'nativeProvisioned':False,'workerCreated':False}
            if response['body']!=expected:raise Conflict('Original canonical reference acceptance differs')
            c.execute('''INSERT INTO hb_administrative_command
                (authority,principal,operation,method,path,wire_digest,accepted_result)
                VALUES(%s,%s,%s,%s,%s,%s,%s)''',
                (row['authority'],actor_id,row['operation'],row['method'],row['path'],row['wire_digest'],Jsonb(response['body'])))
        elif response['status'] not in (400,403,409,422):
            raise BackplaneError('ORIGINAL_HOME_ATTACHMENT_REPLY_UNKNOWN')
        c.execute('UPDATE hb_room_agent_home_outbound SET phase=%s,response=%s WHERE coordinator=%s AND operation=%s',
                  ('answered' if response['status']==200 else 'rejected',Jsonb(response),actor_id,row['operation']))
        return response


async def reconcile(router,request,operation):
    row=await asyncio.to_thread(local,request,router.store,lambda:get(router.store,request['actor'],operation))
    if row is None:return None
    if row['phase']=='rejected':return row['response']
    if row['phase']!='answered':
        if row['phase']!='dispatching':raise BackplaneError('ORIGINAL_HOME_ATTACHMENT_NOT_DISPATCHED')
        reply=await router.call(request,str(row['owner_node']),'room_agent_home_attachment_read',
            [str(row['room']),str(row['node']),operation],verb='GET')
        # An inaccessible/absent read is NOT a definitive rejection of the
        # original write and cannot permit another dispatch.
        if reply['status']!=200:raise BackplaneError('ORIGINAL_HOME_ATTACHMENT_REPLY_UNKNOWN')
        await asyncio.to_thread(local,request,router.store,lambda:finish(router.store,request['actor'],row,reply))
    from .administrative import read
    value=await asyncio.to_thread(local,request,router.store,lambda:read(router.store,request['actor'],operation))
    return {'status':200,'body':value}


async def enroll(router,request,room,node,payload,owner):
    if node!=router.node_id:raise Denied('Send enrollment credential only to its actual HOME HTTP node')
    path=request.path.removeprefix('/hollywood/v3/')
    row=await asyncio.to_thread(local,request,router.store,lambda:get(router.store,request['actor'],payload['operationId']))
    if row is None:
        reply=await router.call(request,owner,'room_agent_allocation_read',[room,node],verb='GET')
        if reply['status']!=200:return reply
        if (reply['body'].get('ownerNodeId'),reply['body'].get('node'),reply['body'].get('coordinator'))!=(owner,node,request['actor']):
            raise Denied('Authenticated allocation owner/home/coordinator differs')
        row=await asyncio.to_thread(local,request,router.store,
            lambda:pin(router.store,request['actor'],room,node,owner,payload,path,reply['body']))
    if (row['method'],row['path'],row['wire_digest'],str(row['owner_node']))!=('POST',path,digest({'method':'POST','path':path,'body':payload}),owner):
        raise Conflict('Original HOME enrollment wire/destination changed')
    if row['phase'] in ('answered','rejected'):return row['response']
    if row['phase']=='preparing':
        row=await prepare(router,request,row,payload['enrollmentCredential'])
    if row['phase']=='prepared':
        await asyncio.to_thread(local,request,router.store,lambda:validate_home_descriptor(router.store,request['actor'],row['descriptor']))
        winner=await asyncio.to_thread(local,request,router.store,lambda:dispatch_intent(router.store,request['actor'],row))
        if winner:
            reply=await router.call(request,owner,'room_agent_home_attach',
                [room,node,row['operation'],row['descriptor']],{'operationId':row['operation']},verb='POST')
            return await asyncio.to_thread(local,request,router.store,lambda:finish(router.store,request['actor'],row,reply))
    # A duplicate HTTP call after lost ACK is also READ ONLY, never resends.
    receipt=await reconcile(router,request,payload['operationId'])
    if receipt['status']!=200:return receipt
    return {'status':200,'body':receipt['body']['acceptedResult']}
