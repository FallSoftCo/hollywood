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


def load(path):
    d = json.loads(Path(path).read_text())
    required = {'authorityId','hostId','servers','passwordFile','caFile'}
    optional = {'localSubjects','trustedViewers','knownAuthorities'}
    if not required <= set(d) or set(d)-required-optional:
        raise ValueError('Invalid federation node configuration')
    identity(d['authorityId']);identity(d['hostId'])
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


async def application(store, config_path):
    from .server import application as http_application
    config,password=load(config_path)
    def enrolled():
        with store.pool.connection() as c:
            row=c.execute('SELECT 1 FROM hb_authority WHERE id=%s',
                          (config['authorityId'],)).fetchone()
            if not row:raise ValueError('Federation node must use its existing local authority')
            # A catalog/API node does not need a model executor enrollment.
            # Actual agent operations still require their existing host-placement
            # binding at AgentRoomTransport's native admission boundary.
    await asyncio.to_thread(enrolled)
    connection=await connect_host(server=config['servers'],host_id=config['hostId'],
        password=password,ca_file=config['caFile'],persistent=True)
    service=None
    try:
        bindings=AgentBindings(store)
        await asyncio.to_thread(bindings.migrate)
        catalog=Catalog(store,connection,authority_id=config['authorityId'],host_id=config['hostId'],
            local_subjects=config.get('localSubjects',{}),trusted_viewers=config.get('trustedViewers',{}),
            known_authorities=config.get('knownAuthorities',[]))
        handlers=AgentRoomTransport(bindings).handlers()
        domain=AgentDomain(bindings)
        handlers["hollywood.agent.authenticate"]=domain.authenticate
        handlers["hollywood.agent.domain"]=domain.invoke
        handlers['hollywood.catalog.read']=catalog.receive
        handlers['hollywood.viewer.room.read']=catalog.receive_room
        service=Service(connection,authority_id=config['authorityId'],host_id=config['hostId'],handlers=handlers)
        await service.start()
        client=Client(connection,authority_id=config['authorityId'],host_id=config['hostId'])
        app=http_application(store,federated_messages=AgentMessages(bindings,client),federated_catalog=catalog,
            federated_agent_gateway=LocalAgentGateway(connection, authority_id=config["authorityId"], host_id=config["hostId"]))
        async def close(app):
            await service.stop()
            await connection.close()
        app.on_cleanup.append(close)
        return app
    except BaseException:
        if service is not None:await service.stop()
        await connection.close()
        raise
