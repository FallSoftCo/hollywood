"""Route existing API calls by resource ownership, never global selection state.

Room and delivery histories remain at their original owner. Read-only lookup can
find an owner; a mutation is sent to exactly one node. A claim destination is
retained before dispatch, and an uncertain response cannot choose another node.
"""
import asyncio
import time
from uuid import UUID,uuid4
from psycopg.types.json import Jsonb

from .backplane import BackplaneError
from .federation_domain import remote_call
from .federation_service import discover
from .store import Conflict,digest

WORK_NODE_HEADER='X-Hollywood-Work-Node'


class NodeRouter:
    def __init__(self, store, gateway):
        self.store,self.gateway=store,gateway
        self.node_id=gateway.node_id
        self._discovered=[];self._discovery_at=0;self._discovery_lock=asyncio.Lock()

    async def nodes(self, authority):
        async with self._discovery_lock:
            if time.monotonic()-self._discovery_at>10:
                self._discovered=await discover(self.gateway.connection,timeout=.3)
                self._discovery_at=time.monotonic()
        return sorted({n['nodeId'] for n in self._discovered if n['authorityId']==authority}|{self.node_id})

    async def call(self,request,node,action,args,body=None,provenance=None,verb=None):
        # A request-local copy prevents a concurrent request or previous chat
        # from choosing this request's destination.
        class Context(dict):
            pass
        context=Context(request)
        context.path=request.path;context.method=verb or request.method
        context['federatedAuthority']=request['actorAuthorityId']
        context['federatedTargetNode']=node
        return await remote_call(self.gateway,context,action,args,body,provenance)

    async def owner(self,request,kind,identity):
        identity=str(UUID(identity));authority=request['actorAuthorityId']
        def local():
            with self.store.pool.connection() as c:
                if kind=='room':
                    row=c.execute('SELECT home_node,room_reference FROM hb_room WHERE id=%s AND authority=%s',
                        (identity,authority)).fetchone()
                    if row:return str(row['home_node'] or self.node_id)
                else:
                    table={'delivery':'hb_delivery','executor':'hb_executor'}.get(kind)
                    if table and c.execute('SELECT 1 FROM '+table+' WHERE id=%s AND authority=%s',
                            (identity,authority)).fetchone():return self.node_id
                row=c.execute('SELECT owner_node FROM hb_federation_resource_route WHERE authority=%s AND kind=%s AND resource=%s',
                    (authority,kind,identity)).fetchone()
                return str(row['owner_node']) if row else None
        known=await asyncio.to_thread(local)
        if known:return known
        action={'room':'room_detail','delivery':'delivery','executor':'executor_read'}[kind]
        args=[identity,'status'] if kind=='executor' else [identity]
        async def lookup(node):
            try:
                reply=await self.call(request,node,action,args,verb="GET")
                return node if reply['status']==200 else None
            except BackplaneError:return None
        found=[n for n in await asyncio.gather(*(lookup(n) for n in await self.nodes(authority) if n!=self.node_id)) if n]
        if len(found)!=1:
            raise BackplaneError('RESOURCE_OWNER_UNAVAILABLE' if not found else 'RESOURCE_OWNER_CONFLICT')
        def remember():
            with self.store.pool.connection() as c:
                c.execute('''INSERT INTO hb_federation_resource_route(authority,kind,resource,owner_node)
                    VALUES(%s,%s,%s,%s) ON CONFLICT(authority,kind,resource) DO UPDATE
                    SET owner_node=EXCLUDED.owner_node,confirmed_at=clock_timestamp()''',(authority,kind,identity,found[0]))
        await asyncio.to_thread(remember);return found[0]

    async def catalog(self,request):
        async def read(node):
            try:
                reply=await self.call(request,node,'catalog',[],verb='GET')
                if reply['status']==200:
                    return node,reply['body'],None
                return node,None,'not_authorized' if reply['status']==403 else 'unavailable'
            except BackplaneError as error:return node,None,error.code
        results=await asyncio.gather(*(read(n) for n in await self.nodes(request['actorAuthorityId'])))
        rooms={};sources=[]
        for node,value,error in results:
            sources.append({'nodeId':node,'authorityId':request['actorAuthorityId'],
                'status':'connected' if value else error})
            if value:
                for room in value['rooms']:
                    key=room['id']
                    if key in rooms and rooms[key]['ownerNodeId']!=node:
                        raise BackplaneError('RESOURCE_OWNER_CONFLICT')
                    rooms[key]={**room,'ownerNodeId':node}
        def remember():
            with self.store.pool.connection() as c:
                for room in rooms.values():
                    c.execute('''INSERT INTO hb_federation_resource_route(authority,kind,resource,owner_node)
                        VALUES(%s,'room',%s,%s) ON CONFLICT(authority,kind,resource) DO UPDATE
                        SET owner_node=EXCLUDED.owner_node,confirmed_at=clock_timestamp()''',
                        (request['actorAuthorityId'],room['id'],room['ownerNodeId']))
        await asyncio.to_thread(remember)
        return {'authorityId':request['actorAuthorityId'],'rooms':list(rooms.values()),'agents':[],
            'sources':sources,'federationStatus':'partial' if any(s['status'] not in {'connected','not_authorized'} for s in sources) else 'connected'}

    async def observations(self,request,args,body):
        async def read(node):
            try:
                reply=await self.call(request,node,'connector_observe',args,body)
                if reply['status']==200:return {**reply['body'],'ownerNodeId':node}
            except BackplaneError:pass
            return None
        rows=[r for r in await asyncio.gather(*(read(n) for n in await self.nodes(request['actorAuthorityId']))) if r]
        if not rows:raise BackplaneError('WORK_SOURCES_UNAVAILABLE')
        eligible=[r for r in rows if r.get('enabled') and r.get('eligibleDeliveryId') and not r.get('busy')]
        # Each source retains its canonical admission order. Node ID is only a
        # stable tie breaker, never a fabricated activity/recency timestamp.
        return min(eligible or rows,key=lambda r:(str((r.get('pending') or {}).get('created_at','')),r['ownerNodeId']))

    async def claim(self,request,args,body):
        authority=request['actorAuthorityId'];actor=request['actor'];operation=args[0]
        fingerprint=digest({'args':list(args),'body':body})
        def previous():
            with self.store.pool.connection() as c:
                return c.execute('SELECT * FROM hb_federation_claim_route WHERE authority=%s AND principal=%s AND operation=%s',
                    (authority,actor,operation)).fetchone()
        old=await asyncio.to_thread(previous)
        if old:
            if old['fingerprint']!=fingerprint:raise Conflict('Original claim contents changed')
            if old['state']=='answered':return old['response']
            # Canonical claim explicitly reconciles the SAME agent/operation
            # before considering new work. This is not a replay of a model/tool
            # action; the SDK separately fences every original native intent.
            node=str(old['owner_node'])
        else:
            projection=await self.observations(request,args[1:],body)
            if not projection.get('eligibleDeliveryId'):
                return {'status':200,'body':{'delivery':None,'admission':projection}}
            node=projection['ownerNodeId']
        def pin():
            with self.store.pool.connection() as c:
                row=c.execute('''INSERT INTO hb_federation_claim_route
                    (authority,principal,operation,owner_node,fingerprint,state)
                    VALUES(%s,%s,%s,%s,%s,'dispatching') ON CONFLICT DO NOTHING RETURNING operation''',
                    (authority,actor,operation,node,fingerprint)).fetchone()
                if not row:raise BackplaneError('ORIGINAL_CLAIM_REPLY_UNKNOWN')
        if not old:await asyncio.to_thread(pin)
        reply=await self.call(request,node,'claim',args,body)
        if reply['status']!=200:
            # A definitive domain rejection is an answer, not an unknown send.
            value=reply
        else:
            result={**reply['body'],'ownerNodeId':node}
            if result.get('delivery'):result['delivery']={**result['delivery'],'ownerNodeId':node}
            value={'status':reply['status'],'body':result}
        def record():
            with self.store.pool.connection() as c:
                c.execute("UPDATE hb_federation_claim_route SET state='answered',response=%s WHERE authority=%s AND principal=%s AND operation=%s",
                    (Jsonb(value),authority,actor,operation))
                if reply['status']==200 and value['body'].get('delivery'):
                    c.execute('''INSERT INTO hb_federation_resource_route(authority,kind,resource,owner_node)
                        VALUES(%s,'delivery',%s,%s) ON CONFLICT DO NOTHING''',(authority,value['body']['delivery']['id'],node))
        await asyncio.to_thread(record)
        return value

    async def destination(self,request,action,args,body,provenance):
        # Use the HTTP router's typed resource capture, not a URL-prefix
        # guess. Connector context/briefing/admission routes also own a
        # delivery even though their first path segment is 'connector'.
        for resource in ('delivery','executor','room'):
            if resource in request.match_info:
                return await self.owner(request,resource,str(UUID(request.match_info[resource])))
        if provenance and provenance.get('deliveryId'):return await self.owner(request,'delivery',provenance['deliveryId'])
        if isinstance(body,dict) and body.get('deliveryId'):return await self.owner(request,'delivery',body['deliveryId'])
        hinted=request.headers.get(WORK_NODE_HEADER)
        if hinted:
            hinted=str(UUID(hinted))
            if hinted not in await self.nodes(request['actorAuthorityId']):raise BackplaneError('RESOURCE_OWNER_UNAVAILABLE')
            return hinted
        return self.node_id
