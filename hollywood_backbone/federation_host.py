"""Host-controller observations over the existing host-local HTTP API and NATS.

This is not agent authority. The existing host policy binds one controller
credential to one source machine; only resource/executor observation operations
are exported. Every invocation rechecks that original binding. No raw bearer,
model input, generic service principal, or execution-start method is forwarded.
"""
import asyncio
import hmac
import json
from uuid import UUID,uuid4
from aiohttp import web

from .backplane import BackplaneError
from .federation_service import Client,discover
from .store import Denied,digest

METHODS={'host_policy':'GET','host_sample':'POST','executor_list':'GET','executor_observe':'POST'}


class HostDomain:
    def __init__(self,store):self.store=store

    def resolve(self,request,payload):
        if not isinstance(payload,dict) or not isinstance(payload.get('credentialDigest'),str):
            raise ValueError('Invalid controller credential envelope')
        host=str(UUID(payload['hostId']))
        if host!=request.source_host_id:raise Denied('Source machine differs')
        with self.store.pool.connection() as c:
            row=c.execute('''SELECT p.id,t.digest FROM hb_host_admission h
                JOIN hb_principal p ON p.id=h.controller AND p.authority=h.authority
                JOIN hb_token t ON t.principal=p.id AND NOT t.revoked
                WHERE h.host=%s AND h.authority=%s AND p.active AND p.kind='service'
                FOR SHARE OF h,p,t''',(host,request.authority_id)).fetchall()
            matches=[r for r in row if hmac.compare_digest(r['digest'],payload['credentialDigest'])]
            if len(matches)!=1:raise Denied('Original controller binding unavailable')
            return str(matches[0]['id'])

    async def authenticate(self,request):
        try:
            if set(request.payload)!={'hostId','credentialDigest'}:raise ValueError('Invalid controller authentication')
            actor=await asyncio.to_thread(self.resolve,request,request.payload)
            return {'id':actor,'kind':'service','authorityId':request.authority_id,'hostId':request.source_host_id}
        except Denied:raise BackplaneError('HOST_ACCESS_DENIED') from None
        except (ValueError,TypeError,KeyError):raise BackplaneError('INVALID_PARAMETERS') from None

    async def invoke(self,request):
        from .server import public_response
        async def execute():
            p=request.payload
            if (set(p)!={'hostId','credentialDigest','action','verb','path','args'}
                    or p['action'] not in METHODS or p['verb']!=METHODS[p['action']]
                    or not isinstance(p['args'],list) or not 1<=len(p['args'])<=4
                    or p['args'][0]!=request.source_host_id
                    or not isinstance(p['path'],str)
                    or not p['path'].startswith('hosts/'+request.source_host_id+'/')):
                raise ValueError('Unsupported host-controller operation')
            actor=await asyncio.to_thread(self.resolve,request,p)
            from .native_effects import transport
            # Use the same transaction fence as the HTTP path: resolving above
            # is not sufficient if a credential is revoked before Store.actor.
            with transport(actor,p['credentialDigest'],p['verb'],p['action'],p['path'],None,None):
                result=await asyncio.to_thread(getattr(self.store,p['action']),actor,*p['args'])
            return web.json_response(result)
        response=await public_response(execute)
        return {'status':response.status,'body':json.loads(response.text)}


async def authenticate(gateway,request):
    """Unknown local host credential can locate its existing canonical binding.

    The host comes from the listener's installed machine identity, never a
    freely chosen peer header. Discovery is cached, authorization is not.
    """
    prefix='/hollywood/v3/hosts/'+gateway.host_id+'/'
    if not request.path.startswith(prefix):raise Denied('Not a local host-controller route')
    path=request.path.removeprefix(prefix)
    if not (path in {'capacity','admission','executors'} or path.startswith('executors/')):
        raise Denied('Unsupported host-controller route')
    lock=getattr(gateway,'_host_discovery_lock',None)
    if lock is None:
        lock=asyncio.Lock();gateway._host_discovery_lock=lock
    import time
    async with lock:
        if time.monotonic()-getattr(gateway,'_host_discovery_at',0)>10:
            gateway._host_services=await discover(gateway.connection,timeout=.3)
            gateway._host_discovery_at=time.monotonic()
        services=list(gateway._host_services)
    payload={'hostId':gateway.host_id,'credentialDigest':digest(request.headers['Authorization'][7:])}
    async def lookup(service):
        try:
            client=Client(gateway.connection,authority_id=service['authorityId'],host_id=gateway.host_id,target_node_id=service['nodeId'])
            actor=await client.request('hollywood.host.authenticate',payload,request_id=str(uuid4()),timeout=2)
            if actor.get('kind')!='service' or actor.get('authorityId')!=service['authorityId'] or actor.get('hostId')!=gateway.host_id:
                raise BackplaneError('RESPONSE_IDENTITY_MISMATCH')
            return actor,service['nodeId']
        except BackplaneError:return None
    found=[r for r in await asyncio.gather(*(lookup(s) for s in services)) if r]
    if len(found)!=1:
        raise Denied('Original controller binding unavailable or ambiguous')
    return found[0]


async def remote_call(gateway,request,action,args):
    if action not in METHODS or request.method!=METHODS[action]:
        raise Denied('Host transport cannot admit agent or administrative operations')
    client=Client(gateway.connection,authority_id=request['federatedHostAuthority'],
        host_id=gateway.host_id,target_node_id=request['federatedHostNode'])
    return await client.request('hollywood.host.domain',{
        'hostId':gateway.host_id,'credentialDigest':request['tokenDigest'],
        'action':action,'verb':request.method,'path':request.path.removeprefix('/hollywood/v3/'),
        'args':list(args)},request_id=str(uuid4()))
