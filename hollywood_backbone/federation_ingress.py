"""Existing host-local HTTP message ingress -> attributed NATS domain admission."""
import asyncio
from uuid import UUID, uuid5

from .store import Denied


class AgentMessages:
    def __init__(self, bindings, router):
        self.bindings, self.router = bindings, router

    async def send(self, *, actor_id, token_digest, room_id, message, native_source):
        transport = self.router.transport
        bound = await asyncio.to_thread(self.bindings.resolve,
            authority_id=transport.authority_id, authenticated_host_id=transport.host_id,
            principal_id=actor_id)
        if bound.token_digest != token_digest:
            raise Denied('Incoming credential does not match current host-agent enrollment')
        # Same HTTP/domain operation always has the same transport identity. No
        # bearer credential or human authority is forwarded to another node.
        request_id = str(uuid5(UUID(transport.authority_id),
            'message:'+actor_id+':'+room_id+':'+message['operationId']))
        return await self.router.request('hollywood.message.send',
            {'actorId': actor_id, 'roomId': room_id, 'message': message,
             'nativeSource': native_source}, request_id=request_id)
