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
      for idx in (0,1):
       v=await get(idx,'rooms');assert {r['id'] for r in v['rooms']}=={p['globalRoomId'] for p in plans};assert {s['nodeId'] for s in v['sources']}=={a.host_id,b.host_id};assert v['federationStatus']=='connected'
       foreign=plans[1-idx]['globalRoomId'];detail=await get(idx,'rooms/'+foreign);assert detail['room']['id']==foreign
       history=await get(idx,'rooms/'+foreign+'/history?view=conversation&limit=1');assert [e['body'] for e in history['events']]==['SYNTHETIC public node '+str(1-idx)]
      # Authorization is not host availability; re-enrollment restores visible rooms.
      saved=catalogs[1].trusted_viewers.pop(subject)
      limited=await get(0,'rooms')
      assert limited['federationStatus']=='connected' and len(limited['rooms'])==1
      assert any(s['nodeId']==b.host_id and s['status']=='not_authorized' for s in limited['sources'])
      catalogs[1].trusted_viewers[subject]=saved
      async def post(idx,path,body,status=200):
       async with client.post(urls[idx]+'/hollywood/v3/'+path,json=body,headers={'Authorization':'Bearer '+guests[idx]['token']}) as response:
        v=await response.json();assert response.status==status,(response.status,v);return v
      foreign=plans[1]['globalRoomId'];target='rooms/'+foreign+'/messages'
      refused={'operationId':'SYNTHETIC-read-only-refused','kind':'message','audience':'room','body':'SYNTHETIC no write grant'}
      await post(0,target,refused,status=403)
      for cat in catalogs:cat.command_viewers[subject]={a.host_id:guests[0]['id'],b.host_id:guests[1]['id']}
      # A previously rejected operation retains its refusal after later grants.
      await post(0,target,refused,status=403)
      request={'operationId':'SYNTHETIC-one-peer-request','kind':'request','audience':'direct','recipientId':plans[1]['credentials']['Global']['principalId'],'body':'SYNTHETIC pending request; no execution placement or worker'}
      accepted=await post(0,target,request);assert accepted['accepted'] and accepted['event']['author']==guests[1]['id']
      assert accepted['event']['data']['humanOrigin']['principalId']==guests[0]['id']
      assert await post(0,target,request)==accepted
      delivery=accepted['deliveries'][0]['id'];read=await get(0,'deliveries/'+delivery);assert read['delivery']['id']==delivery
      await post(0,'deliveries/'+delivery+'/cancel',{})
      assert (await get(0,'deliveries/'+delivery))['delivery']['state']=='cancelled'
      # Simulate a lost reply after receiver acceptance, not another submission.
      original_call=catalogs[0].commands.call
      async def lose_reply(*args,**kwargs):
       value=await original_call(*args,**kwargs)
       if args[3]=='post':
        from hollywood_backbone.backplane import BackplaneError
        raise BackplaneError('REPLY_UNKNOWN')
       return value
      catalogs[0].commands.call=lose_reply
      lost={**request,'operationId':'SYNTHETIC-accepted-lost-reply','kind':'message','body':'SYNTHETIC accepted before lost reply'}
      await post(0,target,lost,status=503);catalogs[0].commands.call=original_call
      receipt=await get(0,'commands/'+lost['operationId']);assert receipt['accepted']
      assert await post(0,target,lost)==receipt
      await post(0,target,{**lost,'body':'SYNTHETIC changed original'},status=409)
      with stores[1].pool.connection() as c:
       assert c.execute('SELECT count(*) n FROM hb_event WHERE id=%s',(receipt['event']['id'],)).fetchone()['n']==1
       assert c.execute('SELECT count(*) n FROM hb_command WHERE principal=%s AND operation=%s',(guests[1]['id'],lost['operationId'])).fetchone()['n']==1
      original_remember=catalogs[0].entry.remember_result
      async def failed_cache(*args,**kwargs):raise RuntimeError('SYNTHETIC cache failure after committed acceptance')
      catalogs[0].entry.remember_result=failed_cache
      cache={**lost,'operationId':'SYNTHETIC-cache-failure','body':'SYNTHETIC accepted despite cache failure'}
      cached=await post(0,target,cache);assert cached['accepted'];catalogs[0].entry.remember_result=original_remember
      assert (await get(0,'commands/'+cache['operationId']))['event']['id']==cached['event']['id']
      from hollywood_backbone.federation_service import Client
      forged=Client(a.connection,authority_id=authority,host_id=a.host_id,target_node_id=b.host_id)
      spoof={'subject':subject,'kind':'human','sourcePrincipal':plans[0]['credentials']['Global']['principalId'],'action':'post','verb':'POST','path':target,'body':request,'args':[foreign,'SYNTHETIC-forged-agent',{'kind':'message','audience':'room','body':'SYNTHETIC agent is not human'}]}
      assert (await forged.request('hollywood.viewer.command',spoof,request_id=str(uuid4())))['status']==403
      # Revoke the source-node grant after receiver admission but before its
      # domain transaction; no request/event may be accepted under the old grant.
      original_post=stores[1].post
      def revoke_before_post(*args,**kwargs):
       catalogs[1].command_viewers[subject].pop(a.host_id)
       return original_post(*args,**kwargs)
      stores[1].post=revoke_before_post
      raced={**lost,'operationId':'SYNTHETIC-revoked-during-admission','body':'SYNTHETIC must not be committed'}
      try:await post(0,target,raced,status=403)
      finally:
       stores[1].post=original_post;catalogs[1].command_viewers[subject][a.host_id]=guests[0]['id']
      with stores[1].pool.connection() as c:
       assert c.execute('SELECT count(*) n FROM hb_command WHERE principal=%s AND operation=%s',(guests[1]['id'],raced['operationId'])).fetchone()['n']==0
      # Concurrent retry observes admitted work; it cannot enter Store.post again.
      import threading
      entered=threading.Event();release=threading.Event();post_count=[]
      def held_post(*args,**kwargs):
       post_count.append(1);entered.set()
       if not release.wait(4):raise RuntimeError('SYNTHETIC bounded hold expired')
       return original_post(*args,**kwargs)
      services[1]._limits['write']=1
      stores[1].post=held_post;concurrent={**lost,'operationId':'SYNTHETIC-concurrent-original','body':'SYNTHETIC one admitted command'}
      first=asyncio.create_task(post(0,target,concurrent))
      try:
       assert await asyncio.to_thread(entered.wait,2)
       pending=await post(0,target,concurrent,status=503);assert pending['mayApply']
       busy={**lost,'operationId':'SYNTHETIC-busy-no-dispatch','body':'SYNTHETIC retry only when original was not admitted'}
       refused_busy=await post(0,target,busy,status=503);assert refused_busy['mayApply'] is False and refused_busy['retryableBeforeDispatch']
       release.set();committed=await first;assert committed['accepted'] and len(post_count)==1
       settled_busy=await post(0,target,busy);assert settled_busy['accepted'] and len(post_count)==2
       with stores[1].pool.connection() as c:
        assert c.execute('SELECT count(*) n FROM hb_command WHERE principal=%s AND operation=%s',(guests[1]['id'],busy['operationId'])).fetchone()['n']==1
      finally:
       release.set();stores[1].post=original_post
       if not first.done():await first
      # Synthetic running metadata exercises existing native UI controls. No
      # app server, execution placement, native process or account is provisioned.
      qrequest={**request,'operationId':'SYNTHETIC-question-original','body':'SYNTHETIC native control scope fixture'}
      qaccepted=await post(0,target,qrequest);qid=qaccepted['deliveries'][0]['id'];agent=plans[1]['credentials']['Global']['principalId']
      from psycopg.types.json import Jsonb
      with stores[1].pool.connection() as c:
       c.execute('INSERT INTO hb_host VALUES(%s,%s,%s,%s,%s)',(b.host_id,authority,'SYNTHETIC host','fixture-only',Jsonb({})))
       c.execute("UPDATE hb_delivery SET state='running',host=%s,generation=1,claim_id='SYNTHETIC-claim',native_thread='SYNTHETIC-thread',native_turn='SYNTHETIC-turn' WHERE id=%s",(b.host_id,qid))
       c.execute('INSERT INTO hb_connector_observation(agent,host,capabilities) VALUES(%s,%s,%s)',(agent,b.host_id,Jsonb(['native-human-controls/v1'])))
       d=c.execute('SELECT * FROM hb_delivery WHERE id=%s',(qid,)).fetchone();e=c.execute('SELECT * FROM hb_event WHERE id=%s',(d['event'],)).fetchone()
       from hollywood_backbone.native_controls import scope
       control_scope=scope(d,e)
      control={'id':str(uuid4()),'operationId':'SYNTHETIC-original-steer','scope':control_scope,'body':'SYNTHETIC explicit steer; no native runtime'}
      guided=await post(0,'deliveries/'+qid+'/guidance',control);assert guided['control']['state']=='pending' and not guided['modelConsumptionProved']
      assert (await get(0,'native-controls/'+control['id']))['control']['id']==control['id']
      attention=str(uuid4());qpayload={'deliveryId':qid,'claimId':'SYNTHETIC-claim','generation':1,'nativeThread':'SYNTHETIC-thread','nativeTurn':'SYNTHETIC-turn','nativeSlot':'SYNTHETIC-question-callback','method':'item/tool/requestUserInput','summary':'SYNTHETIC question','request':{'isBlocking':True,'questions':[{'id':'q','header':'Test','question':'SYNTHETIC choice?','options':None}]}}
      asked=stores[1].attention_create(agent,attention,qpayload);assert asked['canRespond'] is False
      shown=await get(0,'attention/'+attention);assert shown['canRespond']
      answer={'operationId':'SYNTHETIC-question-answer','scope':shown['scope'],'response':{'answers':{'q':{'answers':['SYNTHETIC answer']}}}}
      answered=await post(0,'attention/'+attention+'/response',answer);assert answered['attention']['state']=='response_submitted' and answered['nativeConsumptionProved'] is False
      assert await post(0,'attention/'+attention+'/response',answer)==answered
      await post(0,'attention/'+attention+'/response',{**answer,'scope':{**answer['scope'],'generation':2},'operationId':'SYNTHETIC-stale-scope'},status=409)
      # External handoff stays agent-authored; only protected enrollment names the human.
      from hollywood_backbone.store import digest
      from hollywood_backbone.attention import human_owner
      ext={'id':str(uuid4()),'token':secrets.token_urlsafe(40)}
      owner=plans[1]['credentials']['Owner']['principalId'];room=plans[1]['globalRoomId']
      stores[1].principal(owner,ext['id'],'SYNTHETIC external developer','agent',ext['token'])
      stores[1].membership(owner,room,ext['id'],{'can_read':True,'can_post':True,'can_request':True},0)
      stores[1].communication_set(owner,ext['id'],digest(ext['token']),True,guests[1]['id'])
      handoff={'operationId':'SYNTHETIC-attributed-handoff','kind':'request','audience':'room','recipientId':agent,'body':'SYNTHETIC developer handoff; no worker'}
      async with client.post(urls[1]+'/hollywood/v3/rooms/'+room+'/messages',json=handoff,headers={'Authorization':'Bearer '+ext['token']}) as response:
       hv=await response.json();assert response.status==200,(response.status,hv)
      assert hv['event']['author']==ext['id'] and hv['event']['data']['authorKind']=='agent'
      assert hv['event']['data']['delegatedDecisionOwner']['principalId']==guests[1]['id']
      # Retiring the transport cannot erase already-accepted decision attribution.
      stores[1].communication_set(owner,ext['id'],digest(ext['token']),False)
      with stores[1].pool.connection() as c:
       original=c.execute('SELECT * FROM hb_event WHERE id=%s',(hv['event']['id'],)).fetchone()
       assert str(human_owner(c,original))==guests[1]['id']
      stores[1].cancel(owner,hv['deliveries'][0]['id'])
      # Reopened entry recovers owner/cursor from SQL without a fresh catalog.
      fresh=Catalog(stores[0],a.connection,authority_id=authority,host_id=a.host_id,node_id=a.host_id,node_routing=True,local_subjects={guests[0]['id']:subject},trusted_viewers={subject:guests[0]['id']})
      reopened=await fresh.room_read(guests[0]['id'],'human',authority,plans[1]['globalRoomId'],'history',{'view':'conversation','limit':1});assert len(reopened['events'])==1
      await get(0,'rooms/'+plans[1]['globalRoomId']+'/history',token=plans[0]['credentials']['Global']['token'],status=403)
      # Revoke destination membership after route capture: no cached grant.
      owner=plans[1]['credentials']['Owner']['principalId'];stores[1].membership(owner,plans[1]['globalRoomId'],guests[1]['id'],{'can_read':False,'can_post':False,'can_request':False},1)
      await get(0,'rooms/'+plans[1]['globalRoomId']+'/history',status=403)
      stores[1].membership(owner,plans[1]['globalRoomId'],guests[1]['id'],{'can_read':True,'can_post':True,'can_request':True},2)
      stopped=services.pop(0);await stopped.stop();v=await get(1,'rooms');assert v['federationStatus']=='partial' and len(v['rooms'])==1 and v['rooms'][0]['id']==plans[1]['globalRoomId'];assert any(s['nodeId']==a.host_id and s['status']=='unavailable' for s in v['sources'])
      await get(1,'rooms/'+plans[1]['globalRoomId']+'/history?view=conversation')
     result={'passed':True,'actualPostgreSQL':True,'actualNATS':True,'actualHTTP':True,'sameOrganizationTwoNodes':True,'plainRoomPathsReachCanonicalOwner':True,'directAudiencePreserved':True,'persistedOwnerAfterReopen':True,'revokedMembershipRechecked':True,'agentCannotBecomeHumanViewer':True,'peerLossKeepsLocalAccessible':True,'modelsStarted':0,'productionChanged':False,'readOnlyGrantNeverBecomesWrite':True,'oneCanonicalRequestAndCancel':True,'lostReplyUsesOriginalReceiptWithoutResend':True,'changedOriginalRejected':True,'cacheFailureCannotHideAcceptedSend':True,'agentCannotForgeHumanCommand':True,'grantRecheckedAfterReceiverAdmission':True,'concurrentRetryCannotReenterDomain':True,'boundedRpcBusyIsDefinitivelyNotDispatched':True,'busyRetryKeepsOriginalTargetAndOperation':True,'nativeSteerBoundToOriginalScope':True,'questionReplyBoundToOriginalHumanAndGeneration':True,'syntheticNativeMetadataOnly':True,'externalHandoffRetainsAgentAuthorshipAndExplicitHumanDecisionOwner':True};(out/'qualification.json').write_text(json.dumps(result,indent=2)+'\n');return result
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
