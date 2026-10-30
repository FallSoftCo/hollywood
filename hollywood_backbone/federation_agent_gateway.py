"""Authenticate an original native agent through its host-local Hollywood.

Bearer tokens stay on the local HTTP connection. The owning authority verifies
only the digest against its current exact host/placement/credential binding.
The existing HTTP handlers and canonical domain admission serve both transports.
"""
from uuid import uuid4
from aiohttp import web
from .backplane import BackplaneError, identity
from .federation_service import Client
from .store import Denied, digest

AUTHORITY_HEADER = 'X-Hollywood-Authority'
AGENT_HEADER = 'X-Hollywood-Agent'


class LocalAgentGateway:
    def __init__(self, connection, *, authority_id, host_id, node_id=None):
        self.connection, self.authority_id, self.host_id = connection, authority_id, host_id
        self.node_id = identity(node_id) if node_id is not None else identity(host_id)

    async def authenticate(self, request, authority):
        actor=identity(request.headers.get(AGENT_HEADER,''))
        try:
            result=await Client(self.connection,authority_id=authority,host_id=self.host_id).request(
                'hollywood.agent.authenticate',{'actorId':actor,
                    'credentialDigest':digest(request.headers['Authorization'][7:])},request_id=str(uuid4()))
        except BackplaneError as error:
            if error.code=='AGENT_ACCESS_DENIED':raise Denied('Original host-agent credential denied') from None
            import json
            raise web.HTTPServiceUnavailable(text=json.dumps({'error':error.code,'mayApply':False}),
                content_type='application/json') from None
        if result!={'id':actor,'kind':'agent','authorityId':authority}:
            raise Denied('Original agent identity differs')
        return result

