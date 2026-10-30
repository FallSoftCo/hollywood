"""Persist human command destination before sending; never move unknown intent."""
import asyncio
import logging
from uuid import UUID
from psycopg.types.json import Jsonb
from . import native_effects
from .backplane import BackplaneError
from .store import Denied,Conflict,digest
from .federation_viewer_commands import READS,WRITES

RESOURCES={
 'delivery':('hb_delivery','delivery','deliveries/'),
 'executor':('hb_executor','executor_read','executors/'),
 'attention':('hb_attention','attention_read','attention/'),
 'control':('hb_native_control','native_control_read','native-controls/'),
 'work':('hb_work','work_read','work/'),
 'agent':('hb_principal','principal_read','principals/'),
 'event':('hb_event','inbox_event','inbox/events/')}

class ViewerEntry:
    def __init__(self,catalog):self.catalog=catalog

    async def previous(self,request,operation):
        def read():
            with self.catalog.store.pool.connection() as c:
                self.catalog.store.actor(c,request['actor'])
                return c.execute('SELECT * FROM hb_federation_viewer_command WHERE authority=%s AND principal=%s AND operation=%s',
                    (self.catalog.authority_id,request['actor'],operation)).fetchone()
        with native_effects.transport(request['actor'],request['tokenDigest'],'GET','command',request.path,None,None):
            return await asyncio.to_thread(read)

    async def owner(self,request,kind,identity):
        identity=str(UUID(identity));cat=self.catalog;node=cat.node_catalog.node
        if kind=='room':return await cat.node_catalog.owner(request['actor'],'human',identity)
        table,action,path=RESOURCES[kind]
        def local():
            with cat.store.pool.connection() as c:
                row=c.execute('SELECT * FROM '+table+' WHERE id=%s',(identity,)).fetchone()
                if row:
                    home=row.get('home_node') if kind=='agent' else None
                    return str(row['authority']),str(home or node)
                old=c.execute('''SELECT owner_authority,owner_node FROM hb_federation_viewer_resource
                    WHERE viewer_authority=%s AND viewer=%s AND kind=%s AND resource=%s''',
                    (cat.authority_id,request['actor'],kind,identity)).fetchone()
                return (str(old['owner_authority']),str(old['owner_node'])) if old else None
        known=await asyncio.to_thread(local)
        if known:return known
        async def probe(target):
            try:
                args=[identity,'status'] if kind=='executor' else [identity]
                v=await cat.commands.call(request,*target,action,args,verb='GET',path=path+identity)
                return target if v['status']==200 else None
            except BackplaneError:return None
        found=[v for v in await asyncio.gather(*(probe(t) for t in await cat.node_catalog.nodes())) if v]
        if len(found)!=1:raise BackplaneError('RESOURCE_OWNER_UNAVAILABLE' if not found else 'RESOURCE_OWNER_CONFLICT')
        await self.remember(request,kind,identity,*found[0]);return found[0]

    async def remember(self,request,kind,identity,authority,node):
        def write():
            with self.catalog.store.pool.connection() as c:
                c.execute('''INSERT INTO hb_federation_viewer_resource(viewer_authority,viewer,kind,resource,owner_authority,owner_node)
                    VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(viewer_authority,viewer,kind,resource) DO NOTHING''',
                    (self.catalog.authority_id,request['actor'],kind,identity,authority,node))
        await asyncio.to_thread(write)

    async def remember_result(self,request,response,authority,node):
        body=response.get('body',{})
        if response.get('status')!=200 or not isinstance(body,dict):return
        event=body.get('event')
        if isinstance(event,dict) and event.get('id'):await self.remember(request,'event',event['id'],authority,node)
        for row in body.get('deliveries',[]):
            if isinstance(row,dict) and row.get('id'):await self.remember(request,'delivery',row['id'],authority,node)
        delivery=body.get('delivery')
        if isinstance(delivery,dict) and delivery.get('id'):await self.remember(request,'delivery',delivery['id'],authority,node)

    async def answer(self,request,operation,response):
        def record():
            with self.catalog.store.pool.connection() as c:
                c.execute("UPDATE hb_federation_viewer_command SET state='answered',response=%s WHERE authority=%s AND principal=%s AND operation=%s",
                    (Jsonb(response),self.catalog.authority_id,request['actor'],operation))
        await asyncio.to_thread(record)

    async def receipt(self,request,operation,row):
        if row['state'] in {'answered','not_admitted'}:return row['response']
        reply=await self.catalog.commands.call(request,str(row['owner_authority']),str(row['owner_node']),
            'viewer_receipt',[operation],verb='GET',path='commands/'+operation)
        if reply['status']!=503:
            try:
                await self.answer(request,operation,reply)
                await self.remember_result(request,reply,str(row['owner_authority']),str(row['owner_node']))
            except Exception:
                logging.getLogger(__name__).exception('Accepted remote receipt retained; ingress cache update failed')
        return reply

    async def invoke(self,request,action,args,body):
        cat=self.catalog
        if request['actorKind']!='human' or cat.node_catalog is None or request['actor'] not in cat.local_subjects:
            if action=='inbox' and request.query.get('nodeId'):raise Denied('Node-addressed human inbox enrollment required')
            return None
        if action=='inbox' and request.query.get('nodeId'):
            node=str(UUID(request.query['nodeId']))
            if node==cat.node_catalog.node:return None
            if (cat.authority_id,node) not in await cat.node_catalog.nodes():
                raise Denied('Inbox source node is not discovered in this organization')
            response=await cat.commands.call(request,cat.authority_id,node,action,list(args),verb='GET',path=request.path)
            value=response.get('body',{})
            if response.get('status')==200:
                # A recipient alias is presentation metadata, never event authorship.
                original=value.get('principalId')
                if value.get('authorityId')!=cat.authority_id or not isinstance(value.get('events'),list):
                    raise BackplaneError('RESPONSE_IDENTITY_MISMATCH')
                value={**value,'nodeId':node,'principalId':request['actor'],'sourcePrincipalId':original,
                    'events':[{**h,'principalId':request['actor'],'sourcePrincipalId':original,'nodeId':node} for h in value['events']]}
                response={**response,'body':value}
                identities=[str(UUID(hint['eventId'])) for hint in value['events']]
                if identities:
                    def remember_events():
                        with cat.store.pool.connection() as c:
                            c.cursor().executemany('''INSERT INTO hb_federation_viewer_resource(viewer_authority,viewer,kind,resource,owner_authority,owner_node)
                                VALUES(%s,%s,'event',%s,%s,%s) ON CONFLICT DO NOTHING''',
                                [(cat.authority_id,request['actor'],identity,cat.authority_id,node) for identity in identities])
                    await asyncio.to_thread(remember_events)
            return response
        if action=='command':
            old=await self.previous(request,args[0])
            return await self.receipt(request,args[0],old) if old else None
        if action not in READS|WRITES:return None
        operation=None
        if action in WRITES:
            operation=(body or {}).get('operationId')
            if action=='cancel' and len(args)==1:operation='desired-cancel:'+str(UUID(args[0]))
            if not isinstance(operation,str) or not 1<=len(operation)<=128:raise ValueError('Stable original operation required')
            fingerprint=digest({'action':action,'verb':request.method,'path':request.path,'args':list(args),'body':body})
            old=await self.previous(request,operation)
            if old:
                if old['fingerprint']!=fingerprint:raise Conflict('Original human intent changed')
                if old['state']!='not_admitted':return await self.receipt(request,operation,old)
        resource=next((k for k in ('room','delivery','attention','control','executor','work','agent','event') if k in request.match_info),None)
        if resource is None and isinstance(body,dict) and isinstance(body.get('scope'),dict):
            scope=body['scope']
            resource=next((k for k in ('delivery','room','agent') if scope.get(k+'Id')),None)
            identity=scope.get(resource+'Id') if resource else None
        else:identity=request.match_info[resource] if resource else None
        if resource is None:return None
        if operation is not None and old and old['state']=='not_admitted':
            authority,node=str(old['owner_authority']),str(old['owner_node'])
        else:authority,node=await self.owner(request,resource,identity)
        if (authority,node)==(cat.authority_id,cat.node_catalog.node):return None
        if operation is not None:
            def pin():
                with cat.store.pool.connection() as c:
                    cat.store.actor(c,request['actor'])
                    if old and old['state']=='not_admitted':
                        row=c.execute("UPDATE hb_federation_viewer_command SET state='dispatching',response=NULL WHERE authority=%s AND principal=%s AND operation=%s AND state='not_admitted' AND fingerprint=%s RETURNING operation",
                            (cat.authority_id,request['actor'],operation,fingerprint)).fetchone()
                        if not row:raise BackplaneError('ORIGINAL_VIEWER_REPLY_UNKNOWN')
                        return
                    row=c.execute('''INSERT INTO hb_federation_viewer_command
                        (authority,principal,operation,owner_authority,owner_node,action,resource,fingerprint,state)
                        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,'dispatching') ON CONFLICT DO NOTHING RETURNING operation''',
                        (cat.authority_id,request['actor'],operation,authority,node,action,Jsonb({'kind':resource,'id':identity}),fingerprint)).fetchone()
                    if not row:raise BackplaneError('ORIGINAL_VIEWER_REPLY_UNKNOWN')
            with native_effects.transport(request['actor'],request['tokenDigest'],request.method,action,request.path,body,None):
                await asyncio.to_thread(pin)
        try:reply=await cat.commands.call(request,authority,node,action,args,body)
        except BackplaneError as error:
            if operation is None or error.code not in {'RPC_BUSY_NO_DISPATCH','AUTHORITY_UNAVAILABLE'}:raise
            response={'status':503,'body':{'error':error.code,'mayApply':False,'retryableBeforeDispatch':True,
                'operationId':operation,'next':'retry-original-operation'}}
            def not_admitted():
                with cat.store.pool.connection() as c:
                    c.execute("UPDATE hb_federation_viewer_command SET state='not_admitted',response=%s WHERE authority=%s AND principal=%s AND operation=%s AND state='dispatching' AND fingerprint=%s",
                        (Jsonb(response),cat.authority_id,request['actor'],operation,fingerprint))
            await asyncio.to_thread(not_admitted)
            return response
        if action=='inbox_event' and reply.get('status')==200:
            value=reply['body'];hint=value['hint']
            reply={**reply,'body':{**value,'hint':{**hint,'principalId':request['actor'],'sourcePrincipalId':hint['principalId'],'nodeId':node}}}
        # A refresh/cache failure after acceptance must never turn it into an
        # unconfirmed send. The receiver has its original durable receipt.
        try:
            if operation is not None and reply['status']!=503:await self.answer(request,operation,reply)
            await self.remember_result(request,reply,authority,node)
        except Exception:
            logging.getLogger(__name__).exception('Remote response preserved despite ingress metadata failure')
        return reply
