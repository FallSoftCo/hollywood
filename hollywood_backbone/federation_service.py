"""Thin Hollywood adapter to the standard NATS Services API.

NATS provides discovery, health/stats and queue-group routing. Hollywood retains
its own authorization, canonical receipts, room histories and native lifecycle.
No scheduling, database replication or new execution ownership lives here.
"""
from functools import partial

from nats import micro
from nats.errors import Error as NatsError, TimeoutError, NoRespondersError

from .backplane import HostRequest, BackplaneError, identity, encode, decode


class Service:
    def __init__(self, connection, *, authority_id, host_id, handlers, version='0.3.0', node_id=None, legacy=True):
        self.connection = connection
        self.authority_id, self.host_id = identity(authority_id), identity(host_id)
        self.node_id = identity(node_id) if node_id is not None else self.host_id
        self.legacy = legacy
        self.handlers, self.version = dict(handlers), version
        self.service = None

    @property
    def prefix(self):
        return f'hollywood.v1.{self.authority_id}.service'

    @property
    def node_prefix(self):
        return f'hollywood.v2.{self.authority_id}.node.{self.node_id}.service'

    async def start(self):
        if self.service is not None:
            return
        self.service = await micro.add_service(self.connection, name='hollywood', version=self.version,
            metadata={'authorityId': self.authority_id, 'hostId': self.host_id, 'nodeId': self.node_id,
                      'protocol': 'hollywood-nats/v1' if self.legacy else 'hollywood-nats/v2', 'nodeAddressing': '1'})
        try:
            for operation in self.handlers:
                if not operation or any(ch not in 'abcdefghijklmnopqrstuvwxyz._' for ch in operation):
                    raise ValueError('Invalid service operation')
                await self.service.add_endpoint(name=operation.replace('.', '_')+'_node',
                    subject=f'{self.node_prefix}.*.{operation}',
                    queue_group='hollywood_node_'+self.node_id.replace('-', '_'),
                    handler=partial(self._receive, operation))
                if self.legacy:
                    await self.service.add_endpoint(name=operation.replace('.', '_'),
                        subject=f'{self.prefix}.*.{operation}',
                        queue_group='hollywood_'+self.authority_id.replace('-', '_'),
                        handler=partial(self._receive, operation))
            await self.connection.flush()
        except BaseException:
            await self.stop()
            raise

    async def _receive(self, operation, request):
        request_id = None
        try:
            parts = request.subject.split('.')
            if '.'.join(parts[:6]) == self.node_prefix:
                source_index = 6
            elif self.legacy and '.'.join(parts[:4]) == self.prefix:
                source_index = 4
            else:
                raise BackplaneError('INVALID_ROUTE')
            if '.'.join(parts[source_index+1:]) != operation:
                raise BackplaneError('INVALID_ROUTE')
            source = identity(parts[source_index])  # Broker ACL binds source to host credential.
            raw = decode(request.data)
            if (set(raw) != {'version', 'requestId', 'payload'} or type(raw['version']) is not int
                    or raw['version'] != 1 or not isinstance(raw['payload'], dict)):
                raise BackplaneError('INVALID_ENVELOPE')
            request_id = identity(raw['requestId'])
            result = await self.handlers[operation](HostRequest(self.authority_id, source,
                self.host_id, request_id, operation, raw['payload'], 'normal'))
            try:
                body = encode({'version': 1, 'ok': True, 'requestId': request_id,
                               'hostId': self.host_id, 'nodeId': self.node_id, 'result': result})
            except BackplaneError:
                # Domain work may already be committed. A response-size failure
                # is not a rejection and must never authorize a fresh replay.
                raise BackplaneError('REPLY_UNKNOWN') from None
            await request.respond(body)
        except (ValueError, TypeError, KeyError, BackplaneError) as error:
            code = error.code if isinstance(error, BackplaneError) else 'INVALID_ENVELOPE'
            await request.respond(encode({'version': 1, 'ok': False, 'requestId': request_id, 'error': code}))
        except Exception:
            await request.respond(encode({'version': 1, 'ok': False, 'requestId': request_id,
                                           'error': 'HANDLER_FAILED'}))

    async def stop(self):
        if self.service is not None:
            await self.service.stop()
            self.service = None
        # The caller owns the shared connection and any native runtime.


class Client:
    def __init__(self, connection, *, authority_id, host_id, target_node_id=None):
        self.connection = connection
        self.authority_id, self.host_id = identity(authority_id), identity(host_id)
        self.target_node_id = identity(target_node_id) if target_node_id is not None else None
        self.transport = self  # Existing HTTP ingress needs only these identities.

    async def request(self, operation, payload, *, request_id, timeout=5):
        identity(request_id)
        if not 0 < timeout <= 30:
            raise ValueError('Invalid request timeout')
        subject = (f'hollywood.v2.{self.authority_id}.node.{self.target_node_id}.service.{self.host_id}.{operation}'
                   if self.target_node_id is not None else
                   f'hollywood.v1.{self.authority_id}.service.{self.host_id}.{operation}')
        try:
            reply = await self.connection.request(subject,
                encode({'version': 1, 'requestId': request_id, 'payload': payload}), timeout=timeout)
        except NoRespondersError:
            raise BackplaneError('AUTHORITY_UNAVAILABLE') from None
        except NatsError:
            raise BackplaneError('REPLY_UNKNOWN') from None
        value = decode(reply.data)
        if type(value.get('version')) is not int or value['version'] != 1 or value.get('requestId') != request_id or type(value.get('ok')) is not bool:
            raise BackplaneError('INVALID_RESPONSE')
        if not value['ok']:
            raise BackplaneError(value.get('error', 'REMOTE_FAILED'))
        identity(value['hostId'])
        if self.target_node_id is not None and value.get('nodeId') != self.target_node_id:
            raise BackplaneError('RESPONSE_IDENTITY_MISMATCH')
        return value['result']


async def discover(connection, *, timeout=1):
    """NATS standard $SRV.INFO discovery; no model calls or custom registry lease."""
    import asyncio
    if not 0 < timeout <= 5:
        raise ValueError('Invalid discovery deadline')
    inbox = connection.new_inbox()
    subscription = await connection.subscribe(inbox)
    try:
        await connection.publish('$SRV.INFO.hollywood', reply=inbox)
        await connection.flush()
        deadline = asyncio.get_running_loop().time()+timeout
        found = {}
        while (remaining := deadline-asyncio.get_running_loop().time()) > 0:
            try:
                message = await subscription.next_msg(timeout=remaining)
            except TimeoutError:
                break
            try:
                value = decode(message.data)
                metadata = value.get('metadata', {})
                if value.get('name') != 'hollywood' or metadata.get('protocol') not in {'hollywood-nats/v1','hollywood-nats/v2'}:
                    continue
                authority, host = identity(metadata['authorityId']), identity(metadata['hostId'])
                node = identity(metadata.get('nodeId',host))
                found[(authority,node)] = {'authorityId': authority, 'hostId': host, 'nodeId': node,
                    'instanceId': value['id'], 'version': value['version']}
            except (BackplaneError, ValueError, TypeError, KeyError, AttributeError):
                # One broken announcement must not erase other healthy instances.
                continue
        return list(found.values())
    finally:
        await subscription.unsubscribe()
