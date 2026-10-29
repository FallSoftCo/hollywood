"""Read-only catalog federation through authenticated NATS service instances.

Installation maps a global viewer identity to an existing local principal. It
never imports principals, creates memberships or defaults to an administrator.
The HTTP caller's authenticated kind/identity supplies the outbound assertion;
models and API request bodies cannot choose it. NATS host credentials remain
inside the application, with source-subject ACLs enforced by the broker.
"""
import asyncio

from .backplane import BackplaneError
from .federation_service import Client, discover
from .store import Denied


class Catalog:
    def __init__(self, store, connection, *, authority_id, host_id,
                 local_subjects, trusted_viewers, known_authorities=()):
        self.store, self.connection = store, connection
        self.authority_id, self.host_id = authority_id, host_id
        self.local_subjects, self.trusted_viewers = dict(local_subjects), dict(trusted_viewers)
        self.known_authorities = set(known_authorities)

    def _mapped_read(self, subject, kind):
        local = self.trusted_viewers.get(subject)
        if not local:
            raise Denied('Viewer is not enrolled at this instance')
        with self.store.pool.connection() as c:
            actor = self.store.actor(c, local)
            if actor['kind'] != kind or str(actor['authority']) != self.authority_id:
                raise Denied('Viewer kind/authority does not match local enrollment')
        return self._local_read(local)

    def _local_read(self, actor):
        rooms = self.store.catalog(actor)
        try:
            agents = self.store.agent_directory(actor, None, 100)
        except Denied:
            agents = {'agents': [], 'nextAfter': None, 'access': 'not_authorized'}
        return {**rooms, 'agents': agents['agents'], 'nextAgentAfter': agents['nextAfter']}

    async def receive(self, request):
        try:
            p = request.payload
            if (set(p) != {'subject', 'kind'} or p['kind'] not in {'human', 'agent'}
                    or not isinstance(p['subject'], str) or not 1 <= len(p['subject']) <= 200):
                raise ValueError('Invalid viewer assertion')
            # Only broker-enrolled node services can publish these source subjects.
            # This endpoint is read-only and cannot turn an assertion into input.
            return await asyncio.to_thread(self._mapped_read, p['subject'], p['kind'])
        except Denied:
            raise BackplaneError('VIEWER_ACCESS_DENIED') from None
        except (ValueError, KeyError, TypeError):
            raise BackplaneError('INVALID_PARAMETERS') from None

    async def combined(self, actor_id, kind):
        local = await asyncio.to_thread(self._local_read, actor_id)
        subject = self.local_subjects.get(actor_id)
        if not subject:
            return {**local, 'sources': [{'authorityId': self.authority_id, 'status': 'connected'}],
                    'federationStatus': 'viewer_not_enrolled'}
        services = await discover(self.connection, timeout=.5)
        self.known_authorities.update(s['authorityId'] for s in services)
        authorities = sorted(self.known_authorities - {self.authority_id})
        gate = asyncio.Semaphore(8)
        async def read(authority):
            async with gate:
                client = Client(self.connection, authority_id=authority, host_id=self.host_id)
                from uuid import uuid4
                try:
                    value = await client.request('hollywood.catalog.read',
                        {'subject': subject, 'kind': kind}, request_id=str(uuid4()), timeout=2)
                    if value.get('authorityId') != authority:
                        raise BackplaneError('RESPONSE_IDENTITY_MISMATCH')
                    return authority, value, None
                except BackplaneError as error:
                    return authority, None, error.code
        results = await asyncio.gather(*(read(a) for a in authorities))
        rooms, agents = list(local['rooms']), list(local['agents'])
        sources = [{'authorityId': self.authority_id, 'status': 'connected'}]
        for authority, value, error in results:
            if error:
                sources.append({'authorityId': authority, 'status': 'unavailable', 'reason': error})
                continue
            rooms.extend(value['rooms']);agents.extend(value['agents'])
            sources.append({'authorityId': authority, 'status': 'connected',
                            'nextAgentAfter': value.get('nextAgentAfter')})
        # Keep original authority IDs on every entity; do not manufacture a merged
        # writable authority, strip origin, or reinterpret another instance's cursor.
        return {**local, 'rooms': rooms, 'agents': agents, 'sources': sources,
                'federationStatus': 'partial' if any(s['status']!='connected' for s in sources) else 'connected'}
