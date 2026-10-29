"""Existing authenticated HTTP catalogs over real NATS, two isolated SQL stores."""
import asyncio
import json
import sys
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from aiohttp import ClientSession, web
from hollywood_backbone.bootstrap import initialize
from hollywood_backbone.store import Store
from hollywood_backbone.server import application
from hollywood_backbone.federation_catalog import Catalog
from hollywood_backbone.federation_service import Service
from integration_backplane import qualify


async def main(database,binary,root):
    root.mkdir(mode=0o700,parents=True,exist_ok=False)
    source=json.loads(Path(database).read_text());schemas=[];stores=[];intents=[]
    try:
        for i in range(2):
            schema='qa_federation_catalog_'+uuid4().hex;schemas.append(schema)
            with psycopg.connect(**source,autocommit=True) as c:
                c.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
            config={**source,'options':source.get('options','')+' -c search_path='+schema}
            initialize(config,root/f'node{i}',f'Independent node {i} catalog qualification')
            stores.append(Store(config))
            intents.append(json.loads((root/f'node{i}'/'installation-intent.json').read_text()))
        first,second=[i['authorityId'] for i in intents]
        subject='synthetic-owner-'+uuid4().hex
        def configure(a,b):
            async def check():
                catalogs=[];services=[];runners=[];urls=[]
                try:
                    for idx,transport in enumerate((a,b)):
                        owner=intents[idx]['credentials']['Owner']['principalId']
                        agent=intents[idx]['credentials']['Global']['principalId']
                        # Deliberately mis-map agent to the human subject: remote
                        # kind checking must still deny it, rather than elevate.
                        catalog=Catalog(stores[idx],transport.connection,
                            authority_id=intents[idx]['authorityId'],host_id=transport.host_id,
                            local_subjects={owner:subject,agent:subject},trusted_viewers={subject:owner},
                            known_authorities=[first,second])
                        catalogs.append(catalog)
                        service=Service(transport.connection,authority_id=intents[idx]['authorityId'],
                            host_id=transport.host_id,handlers={'hollywood.catalog.read':catalog.receive,
                                'hollywood.viewer.room.read':catalog.receive_room})
                        await service.start();services.append(service)
                        runner=web.AppRunner(application(stores[idx],federated_catalog=catalog))
                        await runner.setup();runners.append(runner)
                        site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
                        urls.append('http://127.0.0.1:'+str(site._server.sockets[0].getsockname()[1]))
                    async with ClientSession() as session:
                        async def get(idx,role='Owner'):
                            token=intents[idx]['credentials'][role]['token']
                            async with session.get(urls[idx]+'/hollywood/v3/rooms',headers={'Authorization':'Bearer '+token}) as response:
                                assert response.status==200
                                return await response.json()
                        views=[await get(0),await get(1)]
                        expected={intents[0]['globalRoomId'],intents[1]['globalRoomId']}
                        for view in views:
                            assert {r['id'] for r in view['rooms']}==expected,view
                            assert {str(r['authority']) for r in view['rooms']}=={first,second}
                            assert len(view['agents'])==2 and view['federationStatus']=='connected'
                        for entry, destination in ((0,1),(1,0)):
                            token=intents[entry]['credentials']['Owner']['token']
                            room=intents[destination]['globalRoomId']
                            origin=intents[destination]['authorityId']
                            headers={'Authorization':'Bearer '+token}
                            base=urls[entry]+'/hollywood/v3/rooms/'+room
                            async with session.get(base,params={'authority':origin},headers=headers) as response:
                                assert response.status==200,await response.text()
                            async with session.get(base+'/history',params={'authority':origin,'limit':1,'view':'conversation'},headers=headers) as response:
                                assert response.status==200,await response.text()
                                history=await response.json();assert 'events' in history and 'nextCursor' in history
                            agent_headers={'Authorization':'Bearer '+intents[entry]['credentials']['Global']['token']}
                            async with session.get(base+'/history',params={'authority':origin},headers=agent_headers) as response:
                                assert response.status==403,await response.text()
                            async with session.get(base+'/history',params={'authority':origin,'before':1,'after':0},headers=headers) as response:
                                assert response.status==400,await response.text()
                        denied=await get(0,'Global')
                        assert {r['id'] for r in denied['rooms']}=={intents[0]['globalRoomId']}
                        assert any(s.get('reason')=='VIEWER_ACCESS_DENIED' for s in denied['sources'])
                        await services[1].stop()
                        partial=await get(0)
                        assert partial['federationStatus']=='partial'
                        assert {r['id'] for r in partial['rooms']}=={intents[0]['globalRoomId']}
                        assert any(s['authorityId']==second and s['status']=='unavailable' for s in partial['sources'])
                        await services[1].start()
                        restored=await get(0)
                        assert {r['id'] for r in restored['rooms']}==expected
                    result={'passed':True,'actualAuthenticatedHttpThroughEitherNode':True,
                        'independentPostgreSqlSchemasAndAuthorities':2,'sameCombinedRoomsAndAgents':True,
                        'originalAuthorityIdsPreserved':True,'agentCannotAssumeHumanViewer':True,
                        'offlineSourceExplicitAndOtherSourceUsable':True,'reconnectRestoresCatalog':True,
                        'rawCredentialsForwarded':False,'modelsStarted':0,'physicalTwoHostProof':False,
                        'crossAuthorityMutationsQualified':False,
                        'originQualifiedDetailAndHistoryThroughEitherNode':True,
                        'remoteHistoryCannotElevateAgentToHuman':True}
                    (root/'qualification.json').write_text(json.dumps(result,indent=2)+'\n')
                    return result
                finally:
                    for r in runners:await r.cleanup()
                    for s in services:await s.stop()
            return check
        result=await qualify(binary,authority_id=first,configure=configure,additional_authorities=[second])
        print(json.dumps(result,indent=2))
    finally:
        for store in stores:store.close()
        with psycopg.connect(**source,autocommit=True) as c:
            for schema in schemas:c.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


if __name__=='__main__':asyncio.run(main(sys.argv[1],sys.argv[2],Path(sys.argv[3])))
