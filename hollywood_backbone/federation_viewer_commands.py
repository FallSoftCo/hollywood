"""Explicit human command transport; agent/observer credentials confer no access."""
import asyncio,json
from dataclasses import dataclass
from uuid import UUID,uuid4,uuid5
from aiohttp import web
from .backplane import BackplaneError
from .federation_service import Client
from .store import Denied,Conflict,digest
from psycopg.types.json import Jsonb

READS=frozenset({'viewer_receipt','catalog','room_detail','history','delivery','command','execution_context',
 'execution_evidence','executor_read','native_effects','delivery_targets_read','attention_read','attention_inbox',
 'inbox','inbox_event','native_control_read','agent_context','principal_read','agent_directory',
 'role_read','placement_read','decision_list','goal_list','goal_read','work_list','work_read','work_attempts',
 'context_configuration','context_receipt','context_seed_sources','context_seed_source',
 'context_seed_control_read','context_seed_control_content','context_seed_receipt'})
WRITES=frozenset({'post','cancel','attention_response','native_control_create',
 'context_configure','context_seed_mark','context_seed_preview','context_seed_create','context_seed_control'})

@dataclass(frozen=True)
class MappedViewer:
    catalog: object
    subject: str
    source_node: str
    source_principal: str
    principal: str
    authority: str

    @property
    def fingerprint(self):
        return digest(['mapped-human/v1',self.subject,self.source_node,self.source_principal,self.authority,self.principal])

    def validate(self,c,identity,fingerprint):
        expected=self.catalog.command_viewers.get(self.subject,{})
        if (str(identity)!=self.principal or fingerprint!=self.fingerprint
                or expected.get(self.source_node)!=self.source_principal
                or self.catalog.trusted_viewers.get(self.subject)!=self.principal):
            raise Denied('Original human transport grant changed')
        row=c.execute("SELECT * FROM hb_principal WHERE id=%s AND authority=%s AND active AND kind='human' FOR SHARE",
            (self.principal,self.authority)).fetchone()
        if not row:raise Denied('Mapped human is unavailable')
        return row

    def origin(self):
        return {'kind':'human','nodeId':self.source_node,'principalId':self.source_principal,'subject':self.subject}


class ViewerCommands:
    def __init__(self,catalog):self.catalog=catalog

    async def receive(self,request):
        from . import native_effects
        from .server import public_response
        from .federation_domain import kwargs_for
        async def execute():
            p=request.payload
            if (not isinstance(p,dict) or set(p)!={'subject','kind','sourcePrincipal','action','verb','path','body','args'}
                    or p['kind']!='human' or not isinstance(p['subject'],str)
                    or p['action'] not in READS|WRITES or not isinstance(p['args'],list) or len(p['args'])>16
                    or not isinstance(p['path'],str) or len(p['path'])>1024
                    or p['verb'] not in {'GET','POST','PUT','PATCH','DELETE'}
                    or (p['verb']=='GET')!=(p['action'] in READS)):
                raise Denied('Human command transport does not admit this operation')
            source=str(UUID(p['sourcePrincipal']));principal=self.catalog.trusted_viewers.get(p['subject'])
            if not principal or self.catalog.command_viewers.get(p['subject'],{}).get(request.source_host_id)!=source:
                raise Denied('Explicit source-node human command enrollment required')
            bound=MappedViewer(self.catalog,p['subject'],request.source_host_id,source,principal,request.authority_id)
            def receipt():
                with self.catalog.store.pool.connection() as c:
                    self.catalog.store.actor(c,principal)
                    row=c.execute('SELECT * FROM hb_federation_viewer_incoming WHERE authority=%s AND principal=%s AND operation=%s',
                        (request.authority_id,principal,p['args'][0])).fetchone()
                    if row and row['state']=='answered':return row['response']
                    return {'status':503,'body':{'error':'VIEWER_COMMAND_PENDING' if row else 'VIEWER_RECEIPT_ABSENT',
                        'mayApply':True,'operationId':p['args'][0],'next':'read-original-operation'}}
            if p['action']=='viewer_receipt':
                if len(p['args'])!=1 or not isinstance(p['args'][0],str) or not 1<=len(p['args'][0])<=128:
                    raise ValueError('Original operation required')
                with native_effects.transport(principal,bound.fingerprint,'GET','command',p['path'],None,None,admission=bound):
                    return await asyncio.to_thread(receipt)
            operation=None
            if p['action'] in WRITES:
                operation=(p['body'] or {}).get('operationId')
                if p['action']=='cancel' and len(p['args'])==1:operation='desired-cancel:'+str(UUID(p['args'][0]))
                if not isinstance(operation,str) or not 1<=len(operation)<=128:
                    raise ValueError('Stable original human operation ID required')
                fingerprint=digest(p)
                def admit():
                    with self.catalog.store.pool.connection() as c:
                        self.catalog.store.actor(c,principal)
                        inserted=c.execute('''INSERT INTO hb_federation_viewer_incoming
                            (authority,principal,operation,source_node,source_principal,source_subject,fingerprint,rpc_request,state)
                            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,'admitted') ON CONFLICT DO NOTHING RETURNING operation''',
                            (request.authority_id,principal,operation,request.source_host_id,source,p['subject'],fingerprint,request.request_id)).fetchone()
                        row=c.execute('SELECT * FROM hb_federation_viewer_incoming WHERE authority=%s AND principal=%s AND operation=%s FOR SHARE',
                            (request.authority_id,principal,operation)).fetchone()
                        if row['fingerprint']!=fingerprint:raise Conflict('Original human command changed')
                        return inserted is not None,row
                with native_effects.transport(principal,bound.fingerprint,p['verb'],p['action'],p['path'],p['body'],None,admission=bound):
                    fresh,row=await asyncio.to_thread(admit)
                if not fresh:
                    if row['state']=='answered':return row['response']
                    return {'status':503,'body':{'error':'VIEWER_COMMAND_PENDING','operationId':operation,'mayApply':True,'next':'read-original-operation'}}
            async def invoke():
                kwargs=kwargs_for(p['action'],p['verb'],p['path'],p['body'])
                if p['action']=='post':
                    kwargs['wire']={'method':p['verb'],'path':p['path'],'body':p['body'],'humanOrigin':bound.origin()}
                with native_effects.transport(principal,bound.fingerprint,p['verb'],p['action'],p['path'],
                        p['body'],None,admission=bound):
                    value=await asyncio.to_thread(getattr(self.catalog.store,p['action']),principal,*p['args'],**kwargs)
                return web.json_response(value)
            response=await public_response(invoke)
            result={'status':response.status,'body':json.loads(response.text)}
            if operation is not None:
                def answer():
                    # Persist the result of already admitted work. A subsequent
                    # revocation must not erase or reinterpret its acceptance.
                    with self.catalog.store.pool.connection() as c:
                        row=c.execute("UPDATE hb_federation_viewer_incoming SET state='answered',response=%s WHERE authority=%s AND principal=%s AND operation=%s AND fingerprint=%s AND state='admitted' RETURNING operation",
                            (Jsonb(result),request.authority_id,principal,operation,fingerprint)).fetchone()
                        if not row:raise Conflict('Original incoming command receipt changed')
                await asyncio.to_thread(answer)
            return result
        async def envelope():return web.json_response(await execute())
        response=await public_response(envelope)
        value=json.loads(response.text)
        return value if response.status==200 and isinstance(value,dict) and set(value)=={'status','body'} else {'status':response.status,'body':value}

    async def call(self,request,authority,node,action,args,body=None,verb=None,path=None):
        if request['actorKind']!='human':raise Denied('Only authenticated human input may use viewer commands')
        subject=self.catalog.local_subjects.get(request['actor'])
        if not subject:raise Denied('Human viewer has not been enrolled')
        verb=verb or request.method;path=path or request.path.removeprefix('/hollywood/v3/')
        operation=body.get('operationId') if isinstance(body,dict) else None
        identity=str(uuid5(UUID(self.catalog.authority_id),'human:'+request['actor']+':'+verb+':'+path+':'+operation)) if isinstance(operation,str) else str(uuid4())
        return await Client(self.catalog.connection,authority_id=authority,host_id=self.catalog.host_id,target_node_id=node).request(
            'hollywood.viewer.command',{'subject':subject,'kind':'human','sourcePrincipal':request['actor'],
                'action':action,'verb':verb,'path':path,'body':body,'args':list(args)},request_id=identity)
