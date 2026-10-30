"""Read-only catalog federation through authenticated NATS service instances.

Installation maps a global viewer identity to an existing local principal. It
never imports principals, creates memberships or defaults to an administrator.
The HTTP caller's authenticated kind/identity supplies the outbound assertion;
models and API request bodies cannot choose it. NATS host credentials remain
inside the application, with source-subject ACLs enforced by the broker.
"""
import asyncio

from nats.errors import Error as NatsError

from .backplane import BackplaneError
from .federation_service import Client, discover
from .store import Denied


class Catalog:
    def __init__(self, store, connection, *, authority_id, host_id,
                 local_subjects, trusted_viewers, known_authorities=(), node_id=None, node_routing=False, command_viewers=None):
        self.store, self.connection = store, connection
        self.authority_id, self.host_id = authority_id, host_id
        self.local_subjects, self.trusted_viewers = dict(local_subjects), dict(trusted_viewers)
        self.known_authorities = set(known_authorities)
        self.command_viewers={s:dict(nodes) for s,nodes in (command_viewers or {}).items()}
        from .federation_viewer_commands import ViewerCommands
        self.commands=ViewerCommands(self)
        from .federation_viewer_entry import ViewerEntry
        self.entry=ViewerEntry(self)
        from .federation_viewer_catalog import ViewerCatalog
        self.node_catalog=ViewerCatalog(self,node_id or host_id) if node_routing else None

    def _mapped_read(self, subject, kind):
        actor=self._viewer(subject,kind)
        return self.node_catalog.canonical(actor) if self.node_catalog else self._local_read(actor)

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

    def _viewer(self, subject, kind):
        local = self.trusted_viewers.get(subject)
        if not local:
            raise Denied('Viewer is not enrolled at this instance')
        with self.store.pool.connection() as c:
            actor = self.store.actor(c, local)
            if actor['kind'] != kind or str(actor['authority']) != self.authority_id:
                raise Denied('Viewer kind/authority does not match local enrollment')
        return local

    async def receive_room(self, request):
        from uuid import UUID
        try:
            p = request.payload
            if (set(p) != {'subject', 'kind', 'roomId', 'read', 'page'}
                    or p['kind'] not in {'human', 'agent'}
                    or not isinstance(p['subject'], str) or not 1 <= len(p['subject']) <= 200
                    or p['read'] not in {'detail', 'history'} or not isinstance(p['page'], dict)):
                raise ValueError('Invalid room read')
            room = str(UUID(p['roomId']))
            actor = await asyncio.to_thread(self._viewer, p['subject'], p['kind'])
            if p['read'] == 'detail':
                if p['page']: raise ValueError('Detail has no page')
                return await asyncio.to_thread(self.store.room_detail, actor, room)
            page = p['page']
            if set(page) - {'after', 'before', 'limit', 'view'}:
                raise ValueError('Unknown history parameters')
            after, before = page.get('after', 0), page.get('before')
            limit, view = page.get('limit', 100), page.get('view', 'all')
            if (type(after) is not int or not 0 <= after <= 9007199254740991
                    or before is not None and (type(before) is not int or not 1 <= before <= 9007199254740991)
                    or 'after' in page and 'before' in page
                    or type(limit) is not int or not 1 <= limit <= 100
                    or view not in {'all', 'conversation', 'activity'}):
                raise ValueError('Invalid history page')
            return await asyncio.to_thread(self.store.history, actor, room, after, limit, before, view)
        except Denied:
            raise BackplaneError('VIEWER_ACCESS_DENIED') from None
        except (ValueError, TypeError, KeyError):
            raise BackplaneError('INVALID_PARAMETERS') from None

    async def room_location(self, actor_id, kind, room_id):
        if self.node_catalog is None:return None
        authority,node=await self.node_catalog.owner(actor_id,kind,room_id)
        return {'authorityId':authority,'nodeId':node,
                'local':authority==self.authority_id and node==self.node_catalog.node}

    async def room_read(self, actor_id, kind, authority_id, room_id, read, page):
        if self.node_catalog is not None:
            return await self.node_catalog.room_read(actor_id,kind,authority_id,room_id,read,page)
        from uuid import uuid4
        from .backplane import identity
        identity(authority_id); identity(room_id)
        subject = self.local_subjects.get(actor_id)
        if not subject:
            raise BackplaneError('VIEWER_ACCESS_DENIED')
        client = Client(self.connection, authority_id=authority_id, host_id=self.host_id)
        return await client.request('hollywood.viewer.room.read',
            {'subject': subject, 'kind': kind, 'roomId': room_id, 'read': read, 'page': page},
            request_id=str(uuid4()), timeout=2)

    async def combined(self, actor_id, kind):
        if self.node_catalog is not None:return await self.node_catalog.combined(actor_id,kind)
        local = await asyncio.to_thread(self._local_read, actor_id)
        subject = self.local_subjects.get(actor_id)
        if not subject:
            return {**local, 'sources': [{'authorityId': self.authority_id, 'status': 'connected'}],
                    'federationStatus': 'viewer_not_enrolled'}
        try:
            services = await discover(self.connection, timeout=.5)
        except NatsError:
            sources=[{'authorityId': self.authority_id, 'status': 'connected'}]
            sources.extend({'authorityId':a,'status':'unavailable','reason':'NATS_DISCONNECTED'}
                           for a in sorted(self.known_authorities-{self.authority_id}))
            return {**local,'sources':sources,'federationStatus':'partial'}
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
