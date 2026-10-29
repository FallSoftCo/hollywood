"""Shared domain invocation for HTTP and authenticated node transport.

HTTP route/schema validation is retained. NATS does not select arbitrary Store
attributes: only the explicitly exported existing API operations are admitted.
Native provenance, token/placement checks and the existing domain transaction
remain authoritative at the owning store.
"""
import asyncio
import json
import hmac
from uuid import uuid4, uuid5, UUID
from aiohttp import web
from . import native_effects
from .backplane import BackplaneError, identity
from .federation_service import Client
from .store import Denied

METHODS = frozenset(
    ['activity_append',
     'administrative_command',
     'agent_context',
     'agent_directory',
     'agent_execution_binding',
     'agent_resources_read',
     'attention_acknowledge',
     'attention_command',
     'attention_create',
     'attention_inbox',
     'attention_read',
     'attention_response',
     'briefing',
     'cancel',
     'catalog',
     'claim',
     'collaboration_bridge',
     'collaboration_bridge_read',
     'collaboration_call',
     'collaboration_command',
     'command',
     'communication_set',
     'connector_observe',
     'connector_pending',
     'context_call',
     'context_configuration',
     'context_configure',
     'context_receipt',
     'context_seed_control',
     'context_seed_control_content',
     'context_seed_control_read',
     'context_seed_create',
     'context_seed_mark',
     'context_seed_preview',
     'context_seed_receipt',
     'context_seed_source',
     'context_seed_sources',
     'create_room',
     'decision_list',
     'decision_write',
     'delegation',
     'delivery',
     'delivery_edit_call',
     'delivery_edit_read',
     'delivery_edit_reconcile',
     'delivery_recover',
     'delivery_resource_default_read',
     'delivery_targets_read',
     'edit_plan_call',
     'edit_plan_read',
     'execution_context',
     'execution_evidence',
     'executor_attach',
     'executor_begin',
     'executor_list',
     'executor_observe',
     'executor_read',
     'executor_reserve',
     'external_response',
     'external_response_read',
     'goal_list',
     'goal_read',
     'history',
     'host_policy',
     'host_policy_set',
     'host_sample',
     'inbox',
     'inbox_event',
     'membership',
     'mutation_observe',
     'mutation_read',
     'mutation_reconcile',
     'native_control_create',
     'native_control_pending',
     'native_control_read',
     'native_control_update',
     'native_effects',
     'native_goal_binding',
     'physical_agent_grant',
     'physical_grant',
     'physical_targets_read',
     'physical_workspace',
     'placement',
     'placement_read',
     'placement_state',
     'portfolio_dispatch',
     'portfolio_policy_set',
     'portfolio_read',
     'post',
     'principal',
     'principal_read',
     'report_scope_read',
     'resource_default',
     'resource_default_read',
     'resource_roots_read',
     'review_dispatch',
     'role',
     'role_read',
     'room_detail',
     'runtime_observation_read',
     'runtime_observe',
     'supervision',
     'supervision_ack',
     'transition',
     'work_attempts',
     'work_list',
     'work_read',
     'work_resource',
     'work_resource_read',
     'workflow']
)

EDIT_METHODS = frozenset(
    ['delivery_edit_call',
     'delivery_edit_read',
     'delivery_edit_reconcile',
     'edit_plan_call',
     'edit_plan_read',
     'mutation_observe',
     'mutation_read',
     'mutation_reconcile']
)
REPORT_METHODS = frozenset(
    ['report_scope_read']
)


def kwargs_for(action, verb, path, body):
    kwargs = {}
    wire = {'method':verb, 'path':path, 'body':body}
    if action in {'post','workflow','decision_write','report_scope_read'}:
        kwargs['wire'] = wire
    if action in {'context_configure','delivery_recover','delivery_edit_reconcile',
            'mutation_reconcile','report_scope_read','principal','create_room',
            'membership','placement','role','communication_set','collaboration_bridge',
            'physical_workspace','physical_grant','physical_agent_grant','resource_default',
            'work_resource','placement_state'} and isinstance(body,dict) and body.get('operationId'):
        from .store import digest
        kwargs['command']={'operationId':body['operationId'],'method':verb,
            'path':path,'wireDigest':digest(wire)}
    return kwargs


class AgentDomain:
    def __init__(self, bindings):self.bindings=bindings

    async def bound(self, request, payload):
        if not isinstance(payload,dict):raise ValueError('Invalid actor envelope')
        actor=identity(payload['actorId'])
        bound=await asyncio.to_thread(self.bindings.resolve,authority_id=request.authority_id,
            authenticated_host_id=request.source_host_id,principal_id=actor)
        if not isinstance(payload['credentialDigest'],str) or not hmac.compare_digest(
                bound.token_digest,payload['credentialDigest']):
            raise Denied('Incoming credential differs')
        return actor,bound

    async def authenticate(self, request):
        try:
            if set(request.payload)!={'actorId','credentialDigest'}:raise ValueError('Invalid authentication')
            actor,bound=await self.bound(request,request.payload)
            return {'id':actor,'kind':'agent','authorityId':request.authority_id}
        except Denied:raise BackplaneError('AGENT_ACCESS_DENIED') from None
        except (ValueError,TypeError,KeyError):raise BackplaneError('INVALID_PARAMETERS') from None

    async def invoke(self, request):
        from .server import public_response, edit_response, report_response
        async def execute():
            p=request.payload
            if (set(p)!={'actorId','credentialDigest','action','verb','path','body','args','nativeSource'}
                    or p['action'] not in METHODS or not isinstance(p['args'],list)
                    or len(p['args'])>16 or not isinstance(p['path'],str) or len(p['path'])>1024
                    or p['verb'] not in {'GET','POST','PUT','PATCH','DELETE'}):
                raise ValueError('Invalid domain invocation')
            write=p['action'] in native_effects.BUSINESS | native_effects.METADATA
            if (p['verb']=='GET') == write:
                raise ValueError('Domain operation verb differs')
            actor,bound=await self.bound(request,p)
            provenance=native_effects.source(json.dumps(p['nativeSource'])) if p['nativeSource'] is not None else None
            if provenance and (provenance['authorityId']!=request.authority_id
                    or provenance['agentId']!=actor or provenance['hostId']!=request.source_host_id):
                raise Denied('Native source differs from authenticated host-agent')
            with native_effects.transport(actor,bound.token_digest,p['verb'],p['action'],
                    p['path'],p['body'],provenance,admission=bound):
                value=await asyncio.to_thread(getattr(self.bindings.store,p['action']),
                    actor,*p['args'],**kwargs_for(p['action'],p['verb'],p['path'],p['body']))
            return web.json_response(value)
        async def guarded():
            action=request.payload.get('action') if isinstance(request.payload,dict) else None
            if action in EDIT_METHODS:return await edit_response(execute)
            if action in REPORT_METHODS:return await report_response(execute)
            return await execute()
        response=await public_response(guarded)
        return {'status':response.status,'body':json.loads(response.text)}


async def remote_call(gateway, request, action, args, body, provenance):
    from .store import digest
    authority=request['federatedAuthority']
    path=request.path.removeprefix('/hollywood/v3/')
    operation=body.get('operationId') if isinstance(body,dict) else None
    request_id=(str(uuid5(UUID(authority),'domain:'+request['actor']+':'+request.method+':'+path+':'+operation))
        if isinstance(operation,str) else str(uuid4()))
    return await Client(gateway.connection,authority_id=authority,host_id=gateway.host_id).request(
        'hollywood.agent.domain',{'actorId':request['actor'],'credentialDigest':request['tokenDigest'],
            'action':action,'verb':request.method,'path':path,'body':body,
            'args':list(args),'nativeSource':provenance},request_id=request_id)
