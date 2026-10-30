"""Node-scoped human catalog; canonical histories and cursors stay at their owner."""
import asyncio
import time
from uuid import UUID,uuid4

from nats.errors import Error as NatsError
from .backplane import BackplaneError
from .federation_service import Client,discover
from .store import Denied


class ViewerCatalog:
    def __init__(self,catalog,node):
        self.catalog=catalog;self.node=str(UUID(node));self._lock=asyncio.Lock();self._at=0

    async def nodes(self):
        cat=self.catalog
        async with self._lock:
            if time.monotonic()-self._at>10:
                try:rows=await discover(cat.connection,timeout=.3)
                except (NatsError,BackplaneError):rows=[]
                def retain():
                    with cat.store.pool.connection() as c:
                        for row in rows:
                            c.execute('''INSERT INTO hb_federation_known_node(viewer_authority,authority,node)
                                VALUES(%s,%s,%s) ON CONFLICT(viewer_authority,authority,node)
                                DO UPDATE SET observed_at=clock_timestamp()''',
                                (cat.authority_id,row['authorityId'],row['nodeId']))
                await asyncio.to_thread(retain);self._at=time.monotonic()
            def read():
                with cat.store.pool.connection() as c:
                    return c.execute('SELECT authority,node FROM hb_federation_known_node WHERE viewer_authority=%s',
                        (cat.authority_id,)).fetchall()
            rows=await asyncio.to_thread(read)
        return sorted({(str(r['authority']),str(r['node'])) for r in rows}|{(cat.authority_id,self.node)})

    def canonical(self,actor):
        value=self.catalog._local_read(actor)
        ids=[a['id'] for a in value['agents']]
        with self.catalog.store.pool.connection() as c:
            rows=c.execute('SELECT id,home_node,identity_reference FROM hb_principal WHERE id=ANY(%s::uuid[])',
                (ids,)).fetchall() if ids else []
        owning={str(r['id']) for r in rows if not r['identity_reference'] and
                (r['home_node'] is None or str(r['home_node'])==self.node)}
        return {**value,'nodeId':self.node,'agents':[a for a in value['agents'] if a['id'] in owning]}

    async def combined(self,actor,kind):
        cat=self.catalog;local=await asyncio.to_thread(self.canonical,actor)
        subject=cat.local_subjects.get(actor)
        if not subject:
            return {**local,'rooms':[{**r,'ownerNodeId':self.node} for r in local['rooms']],
                'sources':[{'authorityId':cat.authority_id,'nodeId':self.node,'status':'connected'}],
                'federationStatus':'viewer_not_enrolled'}
        gate=asyncio.Semaphore(8)
        async def read(key):
            authority,node=key
            if key==(cat.authority_id,self.node):return key,local,None
            async with gate:
                try:
                    result=await Client(cat.connection,authority_id=authority,host_id=cat.host_id,target_node_id=node).request(
                        'hollywood.catalog.read',{'subject':subject,'kind':kind},request_id=str(uuid4()),timeout=2)
                    if result.get('authorityId')!=authority or result.get('nodeId',node)!=node:
                        raise BackplaneError('RESPONSE_IDENTITY_MISMATCH')
                    return key,result,None
                except BackplaneError as e:return key,None,e.code
        results=await asyncio.gather(*(read(k) for k in await self.nodes()))
        rooms={};agents={};sources=[]
        for (authority,node),value,error in results:
            sources.append({'authorityId':authority,'nodeId':node,'status':'connected' if value is not None else 'unavailable',
                **({'reason':error} if error else {}),
                **({'nextAgentAfter':value.get('nextAgentAfter')} if value is not None else {})})
            if value is None:continue
            for r in value['rooms']:
                key=(authority,r['id'])
                if key in rooms:raise BackplaneError('RESOURCE_OWNER_CONFLICT')
                rooms[key]={**r,'ownerNodeId':node}
            for a in value['agents']:
                key=(authority,a['id'])
                if key in agents:raise BackplaneError('AGENT_HOME_CONFLICT')
                agents[key]={**a,'ownerNodeId':node}
        if len({r['id'] for r in rooms.values()})!=len(rooms):
            raise BackplaneError('RESOURCE_OWNER_CONFLICT')
        def remember():
            with cat.store.pool.connection() as c:
                for (authority,identity),r in rooms.items():
                    c.execute('''INSERT INTO hb_federation_viewer_resource(viewer_authority,viewer,kind,resource,owner_authority,owner_node)
                        VALUES(%s,%s,'room',%s,%s,%s) ON CONFLICT(viewer_authority,viewer,kind,resource) DO UPDATE
                        SET owner_authority=EXCLUDED.owner_authority,owner_node=EXCLUDED.owner_node,
                        observed_at=clock_timestamp()''',(cat.authority_id,actor,identity,authority,r['ownerNodeId']))
        await asyncio.to_thread(remember)
        return {**local,'rooms':list(rooms.values()),'agents':list(agents.values()),'sources':sources,
            'federationStatus':'connected' if all(s['status']=='connected' for s in sources) else 'partial'}

    async def owner(self,actor,kind,room):
        room=str(UUID(room));cat=self.catalog
        def local():
            with cat.store.pool.connection() as c:
                a=cat.store.actor(c,actor)
                if a['kind']!=kind:raise Denied('Authenticated viewer kind changed')
                r=c.execute('SELECT home_node,room_reference FROM hb_room WHERE id=%s AND authority=%s',
                    (room,cat.authority_id)).fetchone()
                if r:return cat.authority_id,str(r['home_node'] or self.node)
                row=c.execute('''SELECT owner_authority,owner_node FROM hb_federation_viewer_resource
                    WHERE viewer_authority=%s AND viewer=%s AND kind='room' AND resource=%s''',(cat.authority_id,actor,room)).fetchone()
                return (str(row['owner_authority']),str(row['owner_node'])) if row else None
        known=await asyncio.to_thread(local)
        if known:return known
        await self.combined(actor,kind)
        known=await asyncio.to_thread(local)
        if known:return known
        raise Denied('No authorized room owner found')

    async def room_read(self,actor,kind,authority,room,read,page):
        owner,node=await self.owner(actor,kind,room)
        if authority is not None and owner!=authority:raise Denied('Room organization differs')
        subject=self.catalog.local_subjects.get(actor)
        if not subject:raise BackplaneError('VIEWER_ACCESS_DENIED')
        return await Client(self.catalog.connection,authority_id=owner,host_id=self.catalog.host_id,target_node_id=node).request(
            'hollywood.viewer.room.read',{'subject':subject,'kind':kind,'roomId':room,'read':read,'page':page},
            request_id=str(uuid4()),timeout=2)
