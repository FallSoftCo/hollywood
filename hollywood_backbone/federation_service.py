"""Thin Hollywood adapter to the standard NATS Services API.

NATS provides discovery, health/stats and queue-group routing. Hollywood retains
its own authorization, canonical receipts, room histories and native lifecycle.
No scheduling, database replication or new execution ownership lives here.
"""
from functools import partial

from nats import micro
from nats.errors import TimeoutError, NoRespondersError, ConnectionClosedError

from .backplane import HostRequest, BackplaneError, identity, encode, decode


class Service:
    def __init__(self, connection, *, authority_id, host_id, handlers, version='0.3.0'):
        self.connection = connection
        self.authority_id, self.host_id = identity(authority_id), identity(host_id)
        self.handlers, self.version = dict(handlers), version
        self.service = None

    @property
    def prefix(self):
        return f'hollywood.v1.{self.authority_id}.service'

    async def start(self):
        if self.service is not None:
            return
        self.service = await micro.add_service(self.connection, name='hollywood', version=self.version,
            metadata={'authorityId': self.authority_id, 'hostId': self.host_id, 'protocol': 'hollywood-nats/v1'})
        try:
            for operation in self.handlers:
                if not operation or any(ch not in 'abcdefghijklmnopqrstuvwxyz._' for ch in operation):
                    raise ValueError('Invalid service operation')
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
            if '.'.join(parts[:4]) != self.prefix or '.'.join(parts[5:]) != operation:
                raise BackplaneError('INVALID_ROUTE')
            source = identity(parts[4])  # Broker ACL binds source subject to host credential.
            raw = decode(request.data)
            if (set(raw) != {'version', 'requestId', 'payload'} or type(raw['version']) is not int
                    or raw['version'] != 1 or not isinstance(raw['payload'], dict)):
                raise BackplaneError('INVALID_ENVELOPE')
            request_id = identity(raw['requestId'])
            result = await self.handlers[operation](HostRequest(self.authority_id, source,
                self.host_id, request_id, operation, raw['payload'], 'normal'))
            await request.respond(encode({'version': 1, 'ok': True, 'requestId': request_id,
                                          'hostId': self.host_id, 'result': result}))
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
    def __init__(self, connection, *, authority_id, host_id):
        self.connection = connection
        self.authority_id, self.host_id = identity(authority_id), identity(host_id)
        self.transport = self  # Existing HTTP ingress needs only these identities.

    async def request(self, operation, payload, *, request_id, timeout=5):
        identity(request_id)
        if not 0 < timeout <= 30:
            raise ValueError('Invalid request timeout')
        subject = f'hollywood.v1.{self.authority_id}.service.{self.host_id}.{operation}'
        try:
            reply = await self.connection.request(subject,
                encode({'version': 1, 'requestId': request_id, 'payload': payload}), timeout=timeout)
        except NoRespondersError:
            raise BackplaneError('AUTHORITY_UNAVAILABLE') from None
        except (TimeoutError, ConnectionClosedError):
            raise BackplaneError('REPLY_UNKNOWN') from None
        value = decode(reply.data)
        if type(value.get('version')) is not int or value['version'] != 1 or value.get('requestId') != request_id or type(value.get('ok')) is not bool:
            raise BackplaneError('INVALID_RESPONSE')
        if not value['ok']:
            raise BackplaneError(value.get('error', 'REMOTE_FAILED'))
        identity(value['hostId'])
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
                if value.get('name') != 'hollywood' or metadata.get('protocol') != 'hollywood-nats/v1':
                    continue
                authority, host = identity(metadata['authorityId']), identity(metadata['hostId'])
                found[(authority,host)] = {'authorityId': authority, 'hostId': host,
                    'instanceId': value['id'], 'version': value['version']}
            except (BackplaneError, ValueError, TypeError, KeyError, AttributeError):
                # One broken announcement must not erase other healthy instances.
                continue
        return list(found.values())
    finally:
        await subscription.unsubscribe()
