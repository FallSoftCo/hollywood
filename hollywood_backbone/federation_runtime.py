"""Opt-in startup of the NATS adapter inside the existing Hollywood application."""
import asyncio
import json
from pathlib import Path

from .backplane import connect_host, identity
from .backplane_auth import AgentBindings
from .backplane_rooms import AgentRoomTransport
from .federation_catalog import Catalog
from .federation_ingress import AgentMessages
from .federation_agent_gateway import LocalAgentGateway
from .federation_domain import AgentDomain
from .federation_service import Service, Client
from .federation_node_router import NodeRouter


def load(path):
    d = json.loads(Path(path).read_text())
    required = {'authorityId','hostId','servers','passwordFile','caFile'}
    optional = {'localSubjects','trustedViewers','knownAuthorities','nodeId','nodeRouting','authorities'}
    if not required <= set(d) or set(d)-required-optional:
        raise ValueError('Invalid federation node configuration')
    identity(d['authorityId']);identity(d['hostId'])
    identity(d.get('nodeId',d['hostId']))
    if 'nodeRouting' in d and type(d['nodeRouting']) is not bool:
        raise ValueError('Node routing must be explicit boolean')
    definitions=d.get('authorities',[{'authorityId':d['authorityId']}])
    if not isinstance(definitions,list) or not 1<=len(definitions)<=32:
        raise ValueError('Bounded served organizations required')
    seen=set()
    for definition in definitions:
        if not isinstance(definition,dict) or set(definition)-{'authorityId','legacyService','localSubjects','trustedViewers','knownAuthorities'}:
            raise ValueError('Invalid served organization')
        authority=identity(definition['authorityId'])
        if authority in seen:raise ValueError('Duplicate served organization')
        seen.add(authority)
        if 'legacyService' in definition and type(definition['legacyService']) is not bool:
            raise ValueError('Legacy service must be explicit boolean')
    if d['authorityId'] not in seen:raise ValueError('Primary organization must remain served')
    for key in ('localSubjects','trustedViewers'):
        mapping=d.get(key,{})
        if not isinstance(mapping,dict):raise ValueError('Invalid viewer enrollment')
        for k,v in mapping.items():
            principal,subject=(k,v) if key=='localSubjects' else (v,k)
            identity(principal)
            if not isinstance(subject,str) or not 1<=len(subject)<=200:
                raise ValueError('Invalid global viewer subject')
    for authority in d.get('knownAuthorities',[]):identity(authority)
    password=Path(d['passwordFile'])
    if not password.is_absolute() or password.stat().st_mode & 0o077:
        raise ValueError('Private host password file must be owner-only')
    return d,password.read_text().strip()


class Catalogs:
    """Existing viewer enrollment remains organization-scoped on a shared node."""
    def __init__(self,store,primary,catalogs):
        self.store,self.authority_id,self.catalogs=store,primary,catalogs
    async def source(self,actor):
        def read():
            with self.store.pool.connection() as c:
                row=c.execute('SELECT authority FROM hb_principal WHERE id=%s AND active',(actor,)).fetchone()
                if not row:raise ValueError('Authenticated actor unavailable')
                return str(row['authority'])
        return self.catalogs[await asyncio.to_thread(read)]
    async def combined(self,actor,kind):
        return await (await self.source(actor)).combined(actor,kind)
    async def room_read(self,actor,kind,*args):
        return await (await self.source(actor)).room_read(actor,kind,*args)


async def application(store, config_path):
    from .server import application as http_application
    config,password=load(config_path)
    definitions=config.get('authorities',[{'authorityId':config['authorityId']}])
    def enrolled():
        with store.pool.connection() as c:
            for definition in definitions:
                if not c.execute('SELECT 1 FROM hb_authority WHERE id=%s',(definition['authorityId'],)).fetchone():
                    raise ValueError('Every served organization must be explicitly installed')
    await asyncio.to_thread(enrolled)
    connection=await connect_host(server=config['servers'],host_id=config['hostId'],
        password=password,ca_file=config['caFile'],persistent=True)
    services=[]
    try:
        bindings=AgentBindings(store)
        await asyncio.to_thread(bindings.migrate)
        catalogs={}
        for definition in definitions:
            authority=definition['authorityId']
            inherited=config if authority==config['authorityId'] else {}
            catalog=Catalog(store,connection,authority_id=authority,host_id=config['hostId'],
                local_subjects=definition.get('localSubjects',inherited.get('localSubjects',{})),
                trusted_viewers=definition.get('trustedViewers',inherited.get('trustedViewers',{})),
                known_authorities=definition.get('knownAuthorities',inherited.get('knownAuthorities',[])))
            catalogs[authority]=catalog
            handlers=AgentRoomTransport(bindings).handlers()
            domain=AgentDomain(bindings)
            handlers['hollywood.agent.authenticate']=domain.authenticate
            handlers['hollywood.agent.domain']=domain.invoke
            from .federation_host import HostDomain
            host_domain=HostDomain(store)
            handlers['hollywood.host.authenticate']=host_domain.authenticate
            handlers['hollywood.host.domain']=host_domain.invoke
            handlers['hollywood.catalog.read']=catalog.receive
            handlers['hollywood.viewer.room.read']=catalog.receive_room
            service=Service(connection,authority_id=authority,host_id=config['hostId'],handlers=handlers,
                node_id=config.get('nodeId',config['hostId']),legacy=definition.get('legacyService',authority==config['authorityId']))
            await service.start();services.append(service)
        gateway=LocalAgentGateway(connection,authority_id=config['authorityId'],host_id=config['hostId'],
            node_id=config.get('nodeId',config['hostId']))
        client=Client(connection,authority_id=config['authorityId'],host_id=config['hostId'])
        app=http_application(store,federated_messages=AgentMessages(bindings,client),
            federated_catalog=Catalogs(store,config['authorityId'],catalogs),federated_agent_gateway=gateway,
            node_router=NodeRouter(store,gateway) if config.get('nodeRouting',False) else None)
        async def close(app):
            for service in services:await service.stop()
            await connection.close()
        app.on_cleanup.append(close)
        return app
    except BaseException:
        for service in services:await service.stop()
        await connection.close()
        raise
