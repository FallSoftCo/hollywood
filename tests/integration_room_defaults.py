"""Real PostgreSQL and HTTP default membership, with an owned disposable DB."""
import asyncio,json,os,secrets
from pathlib import Path
from uuid import uuid4
import psycopg
from psycopg import sql
from aiohttp import web,ClientSession
from hollywood_backbone.bootstrap import initialize
from hollywood_backbone.store import Store
from hollywood_backbone.server import application

async def main():
 os.umask(0o077);root=Path(os.environ['ROOM_DEFAULTS_EVIDENCE']);root.mkdir(mode=0o700)
 original=json.loads(Path(os.environ['ROOM_DEFAULTS_DATABASE_CONFIG']).read_text());database='room_defaults_'+uuid4().hex[:16];config={**original,'dbname':database,'options':str(original.get('options',''))+' -c search_path=public'};created=False;store=None;runner=None
 try:
  with psycopg.connect(**original,autocommit=True) as admin:admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database)))
  created=True;(root/'database.private.json').write_text(json.dumps(config));initialize(config,root/'setup','Synthetic room defaults')
  setup=json.loads((root/'setup/installation-intent.json').read_text());owner=setup['credentials']['Owner'];a=setup['credentials']['Global'];authority=setup['authorityId'];nodea,nodeb=str(uuid4()),str(uuid4());agentb=str(uuid4())
  store=Store(config);store.principal(owner['principalId'],agentb,'Synthetic GPU host','agent',enrollment_credential_digest='a'*64)
  # Fixture home-node allocation only, in the dedicated disposable database.
  with store.pool.connection() as c:
   c.execute('UPDATE hb_principal SET home_node=%s WHERE id=%s',(nodea,a['principalId']));c.execute('UPDATE hb_principal SET home_node=%s WHERE id=%s',(nodeb,agentb))
  runner=web.AppRunner(application(store),access_log=None);await runner.setup();site=web.TCPSite(runner,'127.0.0.1',0);await site.start();base='http://127.0.0.1:'+str(site._server.sockets[0].getsockname()[1])+'/hollywood/v3/'
  async with ClientSession() as session:
   async def call(method,path,body=None,status=200):
    async with session.request(method,base+path,json=body,headers={'Authorization':'Bearer '+owner['token']}) as response:
     v=await response.json();assert response.status==status,(method,path,response.status,v);return v
   async def register(node,agent,enabled,revision,op):return await call('PUT','room-defaults/'+node,{'operationId':op,'agentId':agent,'enabled':enabled,'revision':revision})
   await register(nodea,a['principalId'],True,0,'register-a');rb=await register(nodeb,agentb,True,0,'register-b');assert rb['registration']['revision']==1
   room=str(uuid4());await call('POST','rooms',{'operationId':'room-one','id':room,'ownerId':a['principalId'],'name':'First room','purpose':'Synthetic default membership'})
   sent=await call('POST','rooms/'+room+'/messages',{'operationId':'first-human','kind':'request','audience':'room','body':'Synthetic first input'})
   assert sorted(sent['event']['data']['conversationRecipients'])==sorted([a['principalId'],agentb]);assert sent['deliveries']==[]
   detail=await call('GET','rooms/'+room);member=next(m for m in detail['memberships'] if str(m['principal'])==agentb)
   await call('PUT','rooms/'+room+'/membership',{'operationId':'explicit-revoke-b','principalId':agentb,'can_read':False,'can_post':False,'can_request':False,'revision':member['revision']})
   assert await register(nodeb,agentb,True,0,'register-b')==rb
   second=await call('POST','rooms/'+room+'/messages',{'operationId':'second-human','kind':'request','audience':'room','body':'After explicit revocation'})
   assert second['event']['data']['conversationRecipients']==[a['principalId']]
   await register(nodeb,agentb,False,1,'disable-b')
   room2=str(uuid4());await call('POST','rooms',{'operationId':'room-two','id':room2,'ownerId':a['principalId'],'name':'Second room','purpose':'Disabled defaults'})
   third=await call('POST','rooms/'+room2+'/messages',{'operationId':'third-human','kind':'request','audience':'room','body':'Disabled default remains absent'})
   assert third['event']['data']['conversationRecipients']==[a['principalId']]
   await call('PUT','room-defaults/'+nodeb,{'operationId':'stale-change','agentId':agentb,'enabled':True,'revision':0},409)
   with store.pool.connection() as c:
    assert c.execute('SELECT count(*) AS n FROM hb_delivery').fetchone()['n']==0
    assert c.execute('SELECT count(*) AS n FROM hb_executor').fetchone()['n']==0
   result={'state':'passed','firstHumanFanoutAlreadyIncludesBothHostAgents':True,'noModelPollOrExecutionAdmission':True,'explicitRoomRevocationPreserved':True,'originalRegistrationReceiptReconciled':True,'disabledPolicyExcludedFromNewRoom':True,'staleRevisionRejected':True,'disposableDatabase':database,'productionTablesChanged':False,'modelCalls':0}
   (root/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
 finally:
  if runner:await runner.cleanup()
  if store:store.close()
  if created:
   with psycopg.connect(**original,autocommit=True) as admin:admin.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(database)))
   (root/'cleanup.json').write_text(json.dumps({'ownedDatabaseDropped':database,'productionRestarted':False})+'\n')
asyncio.run(main())
