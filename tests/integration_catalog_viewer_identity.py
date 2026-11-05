"""Same-organization viewer access through independent SQL/NATS/HTTP nodes."""
import asyncio,json,secrets,sys
from pathlib import Path
from uuid import uuid4
import psycopg
from psycopg import sql
from aiohttp import web,ClientSession
from hollywood_backbone.store import Store
from hollywood_backbone.server import application
from hollywood_backbone.federation_catalog import Catalog
from hollywood_backbone.federation_service import Service
from integration_backplane import qualify

async def main(database,binary,out):
 out.mkdir(mode=0o700,parents=True,exist_ok=False);source=json.loads(Path(database).read_text());authority=str(uuid4());stores=[];schemas=[];plans=[]
 try:
  for i in range(2):
   schema='qa_viewer_node_'+uuid4().hex;schemas.append(schema)
   with psycopg.connect(**source,autocommit=True) as c:c.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
   store=Store({**source,'options':source.get('options','')+' -c search_path='+schema});stores.append(store);store.migrate()
   plan={'globalRoomId':str(uuid4()),'credentials':{n:{'principalId':str(uuid4()),'token':secrets.token_urlsafe(40)} for n in ('Owner','Global')}};plans.append(plan);store.bootstrap(authority,'Synthetic node '+str(i),plan)
  def configure(a,b):
   async def check():
    catalogs=[];services=[];runners=[];urls=[];guests=[];subject='SYNTHETIC-viewer-'+uuid4().hex
    try:
     for idx,t in enumerate((a,b)):
      store=stores[idx];owner=plans[idx]['credentials']['Owner']['principalId'];g={'id':str(uuid4()),'token':secrets.token_urlsafe(40)};guests.append(g);store.principal(owner,g['id'],'Synthetic viewer','human',g['token'])
      with store.pool.connection() as c:
       c.execute('UPDATE hb_principal SET home_node=%s',(t.host_id,));c.execute('UPDATE hb_room SET home_node=%s',(t.host_id,))
      store.membership(owner,plans[idx]['globalRoomId'],g['id'],{'can_read':True,'can_post':True,'can_request':True},0)
      store.post(owner,plans[idx]['globalRoomId'],'public-'+str(idx),{'kind':'message','audience':'room','body':'SYNTHETIC public node '+str(idx)})
      store.post(owner,plans[idx]['globalRoomId'],'private-'+str(idx),{'kind':'message','audience':'direct','recipientId':plans[idx]['credentials']['Global']['principalId'],'body':'SYNTHETIC private excluded'})
      cat=Catalog(store,t.connection,authority_id=authority,host_id=t.host_id,node_id=t.host_id,node_routing=True,local_subjects={g['id']:subject,plans[idx]['credentials']['Global']['principalId']:subject},trusted_viewers={subject:g['id']});catalogs.append(cat)
      service=Service(t.connection,authority_id=authority,host_id=t.host_id,node_id=t.host_id,legacy=False,handlers={'hollywood.catalog.read':cat.receive,'hollywood.viewer.room.read':cat.receive_room,'hollywood.viewer.command':cat.commands.receive});await service.start();services.append(service)
      runner=web.AppRunner(application(store,federated_catalog=cat));await runner.setup();runners.append(runner);site=web.TCPSite(runner,'127.0.0.1',0);await site.start();urls.append('http://127.0.0.1:'+str(site._server.sockets[0].getsockname()[1]))
     async with ClientSession() as client:
      async def get(idx,path,token=None,status=200):
       async with client.get(urls[idx]+'/hollywood/v3/'+path,headers={'Authorization':'Bearer '+(token or guests[idx]['token'])}) as response:
        v=await response.json();assert response.status==status,(response.status,v);return v
      viewer_keys=[]
      assert guests[0]['id']!=guests[1]['id']
      for idx in (0,1):
       v=await get(idx,'rooms');assert {r['id'] for r in v['rooms']}=={p['globalRoomId'] for p in plans};assert {s['nodeId'] for s in v['sources']}=={a.host_id,b.host_id};assert v['federationStatus']=='connected'
       proof=v['viewerIdentity'];assert proof['version']==1 and proof['authorityId']==authority and len(proof['viewerId'])==64
       assert subject not in json.dumps(proof) and guests[idx]['id'] not in json.dumps(proof)
       viewer_keys.append(proof['viewerId'])
       foreign=plans[1-idx]['globalRoomId'];detail=await get(idx,'rooms/'+foreign);assert detail['room']['id']==foreign
       history=await get(idx,'rooms/'+foreign+'/history?view=conversation&limit=1');assert [e['body'] for e in history['events']]==['SYNTHETIC public node '+str(1-idx)]
      assert viewer_keys[0]==viewer_keys[1]
      assert catalogs[0].viewer_identity(guests[0]['id'],'agent') is None
      assert catalogs[0].viewer_identity(plans[0]['credentials']['Global']['principalId'],'human') is None
      different=subject+'-other';catalogs[1].local_subjects[guests[1]['id']]=different;catalogs[1].trusted_viewers[different]=guests[1]['id']
      assert catalogs[1].viewer_identity(guests[1]['id'],'human')['viewerId']!=viewer_keys[0]
      catalogs[1].local_subjects[guests[1]['id']]=subject;catalogs[1].trusted_viewers.pop(different)
      owner=plans[1]['credentials']['Owner']['principalId']
      stores[1].membership(owner,plans[1]['globalRoomId'],guests[1]['id'],{'can_read':False,'can_post':False,'can_request':False},1)
      await get(0,'rooms/'+plans[1]['globalRoomId']+'/history',status=403)
      agent_catalog=await get(0,'rooms',token=plans[0]['credentials']['Global']['token'])
      assert 'viewerIdentity' not in agent_catalog and all(r['id']==plans[0]['globalRoomId'] for r in agent_catalog['rooms'])
     result={'passed':True,'actualPostgreSQL':True,'actualNATS':True,'actualHTTP':True,'sharedViewerAcrossDistinctLocalHumans':True,'distinctViewerAndAgentNotMerged':True,'privateSubjectNotExposed':True,'revokedMembershipRechecked':True,'modelsStarted':0,'commandsSubmitted':0,'productionChanged':False}
     (out/'qualification.json').write_text(json.dumps(result,indent=2)+'\n');return result
    finally:
     for runner in runners:await runner.cleanup()
     for service in services:await service.stop()
   return check
  result=await qualify(binary,authority_id=authority,configure=configure);print(json.dumps(result,indent=2))
 finally:
  for store in stores:store.close()
  with psycopg.connect(**source,autocommit=True) as c:
   for schema in schemas:c.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))

if __name__=='__main__':asyncio.run(main(sys.argv[1],sys.argv[2],Path(sys.argv[3])))
