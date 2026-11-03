import asyncio,json,os,secrets
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
from aiohttp import ClientSession
from aiohttp.test_utils import TestServer
from hollywood_backbone.store import Store,Conflict
from hollywood_backbone.server import application
from hollywood_backbone.federation_node_router import NodeRouter
from hollywood_backbone.conversation_inbox import feed

async def main():
 os.umask(0o077);root=Path(os.environ['ROOM_ARCH_QA']);store=Store(json.loads((root/'database.json').read_text()));store.migrate()
 uid=lambda:str(uuid4());authority,globalroom,cpu,mac,macagent,legacy,newroom=[uid() for _ in range(7)]
 creds={k:{'principalId':uid(),'token':secrets.token_urlsafe(40)} for k in ['Owner','Global']}
 store.bootstrap(authority,'ISOLATED origin QA',{'globalRoomId':globalroom,'credentials':creds})
 owner=creds['Owner']['principalId'];control=creds['Global']['principalId'];mt=secrets.token_urlsafe(40)
 store.principal(owner,macagent,'ISOLATED Mac Global','agent',mt,administrator=True)
 with store.pool.connection() as c:
  c.execute('UPDATE hb_principal SET home_node=%s WHERE id=%s',(cpu,control));c.execute('UPDATE hb_principal SET home_node=%s WHERE id=%s',(mac,macagent))
 store.placement(owner,cpu,'CPU original environment','cpu-fixed',{},control);store.placement(owner,mac,'MacBook Pro environment','mac-fixed',{},macagent)
 with store.pool.connection() as c:
  # Exact historical legacy shape: seed retained row/grants, not current creation.
  c.execute("INSERT INTO hb_room(id,authority,name,purpose,owner) VALUES(%s,%s,'ISOLATED legacy room','Preserve original sources',%s)",(legacy,authority,control))
  for actor in (owner,control):c.execute('INSERT INTO hb_membership(authority,room,principal,can_read,can_post,can_request) VALUES(%s,%s,%s,true,true,true)',(authority,legacy,actor))
 old=store.post(owner,legacy,uid(),{'kind':'request','body':'OLD INPUT MUST NOT REPLAY','audience':'room'})['event']
 role_before=store.role_read(owner,control)
 early=uid();store.create_room(owner,early,'ISOLATED room before environments','Pending explicit host enrollment',control)
 assert store.room_agents_read(owner,early)['generation']['phase']=='active'
 assert not store.room_agents_read(owner,early)['complete']
 assert not any(m['principal']==control for m in store.room_detail(owner,early)['memberships'])
 for node,agent in [(cpu,control),(mac,macagent)]:store.room_default_set(owner,node,{'agentId':agent,'enabled':True,'revision':0})
 store.create_room(owner,newroom,'ISOLATED new origin room','Real initialization metadata',control)
 with store.pool.connection() as c:
  assert c.execute('SELECT count(*) AS n FROM hb_membership WHERE room=%s AND principal IN (%s,%s)',(newroom,control,macagent)).fetchone()['n']==0
 before=store.post(owner,newroom,uid(),{'kind':'request','body':'NEW INPUT BEFORE HOME ENROLLMENT','audience':'room'})
 assert before['deliveries']==[]
 plan=store.room_agents_read(owner,newroom);assert len(plan['agents'])==2 and not plan['complete']
 ids={r['node']:r['agent'] for r in plan['agents']};assert set(before['event']['data']['conversationRecipients'])==set(ids.values())
 router=NodeRouter(store,SimpleNamespace(node_id=cpu))
 async def nodes(_authority):return [cpu]
 async def local_catalog(request,node,action,args,body=None,provenance=None,verb=None):
  assert node==cpu and action=='catalog';return {'status':200,'body':store.catalog(request['actor'])}
 router.nodes=nodes;router.call=local_catalog
 api=TestServer(application(store,node_router=router));await api.start_server()
 headers={'Authorization':'Bearer '+creds['Global']['token']};token=secrets.token_urlsafe(40)
 try:
  async with ClientSession(headers=headers) as session:
   async def call(tail,body=None):
    async with session.request('GET' if body is None else 'POST',api.make_url('/hollywood/v3/'+tail),json=body) as r:return r.status,await r.json()
   path='rooms/'+newroom+'/environment-agents'
   st,value=await call(path,{'operationId':'original-init'});assert st==200,(st,value)
   st,again=await call(path,{'operationId':'original-init'});assert st==200 and again==value
   st,value=await call(path+'/'+cpu+'/enroll',{'operationId':'original-enroll','enrollmentCredential':token});assert st==200,(st,value)
   st,again=await call(path+'/'+cpu+'/enroll',{'operationId':'original-enroll','enrollmentCredential':token});assert st==200 and again==value
   ready=store.room_agents_read(owner,newroom);assert not ready['complete'] and all(not a['ready'] for a in ready['agents'])
   agent=ids[cpu];role=store.role_read(owner,agent);assert str(role['home_room'])==newroom and role['primary_role']=='room-agent'
   assert store.principal_read(agent,agent)['administrator'] is False
   events=feed(store,agent,0,100)['events'];assert [e['id'] for e in events]==[before['event']['id']]
   assert old['id'] not in [e['id'] for e in events]
   try:store.role(owner,agent,'room-agent','Different room',globalroom,role['revision'])
   except Conflict:pass
   else:raise AssertionError('Origin moved')
   st,cat=await call('rooms');assert st==200 and any(r['room']==newroom for r in cat['roomAgentInitializations'])
   # Attempt remote home through its actual coordinator identity at this CPU.
   async with session.post(api.make_url('/hollywood/v3/'+path+'/'+mac+'/enroll'),json={'operationId':'remote-not-forged','enrollmentCredential':secrets.token_urlsafe(40)},headers={'Authorization':'Bearer '+mt}) as r:
    assert r.status==409,(r.status,await r.json())
   row=next(r for r in store.room_agents_read(owner,newroom)['agents'] if r['node']==cpu)
   payload={'operationId':'host-proof','revision':row['revision'],'state':'provisioning','note':'SYNTHETIC native host receipt; not real native qualification','receipt':{'nativeThreadId':'SYNTHETIC-distinct-cpu-thread','nativeHostId':cpu,'appServerManifestSha256':'1'*64,'sharedAppServer':True}}
   st,value=await call(path+'/'+cpu+'/report',payload);assert st==200,(st,value);assert not value['complete']
   store.conversation_state_observe(agent,newroom,{'observationType':'nativeState','nativeThread':'SYNTHETIC-distinct-cpu-thread','revision':1,'status':{'type':'idle','activeFlags':[]}})
   observed=store.room_agents_read(owner,newroom);assert next(r for r in observed['agents'] if r['node']==cpu)['ready'] and not observed['complete']
   detail=store.room_detail(owner,newroom);who=next(a for a in detail['actors'] if a['id']==agent);assert who['identity']['host']['name']=='CPU original environment' and who['identity']['originRoomId']==newroom
   with store.pool.connection() as c:c.execute("UPDATE hb_conversation_state SET observed_at=statement_timestamp()-interval '80 seconds' WHERE agent=%s",(agent,))
   assert not next(r for r in store.room_agents_read(owner,newroom)['agents'] if r['node']==cpu)['ready']
   assert store.role_read(owner,control)==role_before
   # Retained room adoption is staged: even an enrolled successor does not
   # receive a new public input until an explicit all-ready owner handoff.
   global_members_before=store.room_detail(owner,globalroom)['memberships']
   store.room_default_set(owner,mac,{'agentId':macagent,'enabled':False,'revision':1})
   store.room_agents_initialize(owner,legacy)
   staged=store.room_agents_read(owner,legacy);assert staged['generation']['phase']=='staging'
   candidate=staged['agents'][0]['agent'];assert staged['agents'][0]['node']==cpu
   st,value=await call('rooms/'+legacy+'/environment-agents/'+cpu+'/enroll',{'operationId':'legacy-home-enroll','enrollmentCredential':secrets.token_urlsafe(40)});assert st==200,(st,value)
   staged_input=store.post(owner,legacy,uid(),{'kind':'request','body':'STAGED OLD FLEET ONLY','audience':'room'})['event']
   assert staged_input['data']['conversationRecipients']==[control]
   assert feed(store,candidate,0,100)['events']==[]
   retirement={'operationId':'explicit-legacy-handoff','revision':staged['generation']['revision'],'retiringAgentIds':[control],'handoff':{'originalWorkDisposition':'retained-with-original-owners','ownerReceiptSha256':'2'*64}}
   st,value=await call('rooms/'+legacy+'/environment-agents/activate',retirement);assert st==409,(st,value)
   row=store.room_agents_read(owner,legacy)['agents'][0]
   st,value=await call('rooms/'+legacy+'/environment-agents/'+cpu+'/report',{'operationId':'synthetic-legacy-ready','revision':row['revision'],'state':'provisioning','note':'SYNTHETIC bootstrap only','receipt':{'nativeThreadId':'SYNTHETIC-legacy-successor','nativeHostId':cpu,'appServerManifestSha256':'1'*64,'sharedAppServer':True}});assert st==200,(st,value)
   store.conversation_state_observe(candidate,legacy,{'observationType':'nativeState','nativeThread':'SYNTHETIC-legacy-successor','revision':1,'status':{'type':'idle','activeFlags':[]}})
   assert store.room_agents_read(owner,legacy)['nativeReady'] and not store.room_agents_read(owner,legacy)['complete']
   st,value=await call('rooms/'+legacy+'/environment-agents/activate',retirement);assert st==200,(st,value)
   old_actor=next(a for a in store.room_detail(owner,legacy)['actors'] if a['id']==control);assert old_actor['participation']=='retained-source-owner' and not old_actor['can_post'] and not old_actor['can_request']
   st,again=await call('rooms/'+legacy+'/environment-agents/activate',retirement);assert st==200 and again==value
   successor_input=store.post(owner,legacy,uid(),{'kind':'request','body':'ACCEPTED NEW FLEET ONLY','audience':'room'})['event']
   assert successor_input['data']['conversationRecipients']==[candidate]
   assert [e['id'] for e in feed(store,candidate,0,100)['events']]==[successor_input['id']]
   retained=next(e for e in feed(store,control,0,100)['events'] if e['id']==staged_input['id'])
   assert retained==staged_input  # accepted historical recipients/body unchanged
   private=store.post(owner,legacy,uid(),{'kind':'request','body':'OLD PRIVATE SOURCE STILL OWNED','audience':'direct','recipientId':control})['event']
   assert private['data']['conversationRecipients']==[control]
   # The actual host enable/reconnect path allocates its origin identity; it
   # never auto-joins its Global or overwrites a membership revocation.
   store.room_default_set(owner,mac,{'agentId':macagent,'enabled':True,'revision':2})
   current=store.room_agents_read(owner,legacy);assert len(current['agents'])==2 and not current['complete']
   assert store.room_detail(owner,globalroom)['memberships']==global_members_before
   assert not any(m['principal']==macagent for m in store.room_detail(owner,legacy)['memberships'])
   new_id=next(r['agent'] for r in current['agents'] if r['node']==mac)
   after_join=store.post(owner,legacy,uid(),{'kind':'request','body':'PENDING NEW HOST QUEUED ONCE','audience':'room'})['event']
   assert set(after_join['data']['conversationRecipients'])=={candidate,new_id}
   # A remote home reference is non-administrator at the receiving node.
   # First exercise that exact permission shape without pretending NATS ran.
   outsider=uid();store.principal(owner,outsider,'Nonadmin registered home control','agent',secrets.token_urlsafe(40))
   extra_node=uid()
   with store.pool.connection() as c:c.execute('UPDATE hb_principal SET home_node=%s WHERE id=%s',(extra_node,outsider))
   store.placement(owner,extra_node,'Third actual registered QA environment','third-fixed',{},outsider)
   store.room_default_set(owner,extra_node,{'agentId':outsider,'enabled':True,'revision':0})
   assert store.principal_read(owner,outsider)['administrator'] is False
   local_catalog=store.catalog(outsider)
   assert local_catalog['rooms']==[] and local_catalog['roomAgentInitializations']
   assert all(r['node']==extra_node for r in local_catalog['roomAgentInitializations'])
   scoped=store.room_agents_read(outsider,newroom)
   assert scoped['controlOnly'] and len(scoped['agents'])==1 and scoped['agents'][0]['node']==extra_node
   try:store.room_detail(outsider,newroom)
   except Exception as error:assert type(error).__name__=='Denied'
   else:raise AssertionError('Initialization metadata accidentally granted room history')
   store.room_default_set(owner,extra_node,{'agentId':outsider,'enabled':False,'revision':1})
   assert store.catalog(outsider)['roomAgentInitializations']==[]
   try:store.room_agents_read(outsider,newroom)
   except Exception as error:assert type(error).__name__=='Denied'
   else:raise AssertionError('Revoked coordinator still reads desired metadata')
   result={'passed':True,'realPgAndHttp':True,'stableRoomEnvironmentIdentities':True,'lostAckSameMetadataOperationReused':True,'desiredRecipientRecordedBeforeEnrollment':True,'noPreInitializationInputReplay':True,'newGlobalMemberships':0,'stagedSuccessorReceivesNoOldOrStagingInput':True,'explicitAllReadyHandoffAtomicallyChangesFutureFanout':True,'oldPrivateOwnerAndHistoricalRecipientsRetained':True,'environmentReenableAllocatesOriginWithoutGlobalJoin':True,'typedGlobalBootstrapExcludedFromProjectReconcile':True,'projectBeforeHostRegistrationQueuesDesiredOnceRegistered':True,'noForeignHomeCredentialManufactured':True,'registeredNonadminHomeSeesOnlyOwnSlotsWithoutRoomHistoryOrAdminPromotion':True,'coordinatorRevocationFencesMetadataDiscovery':True,'originMoveRejected':True,'readyRequiresFreshMatchingObservation':True,'offlineEnvironmentNotCountedReady':True,'hostAndOriginDescriptorProjected':True,'originalGlobalRoleUnchanged':True,'nativeAndHostProof':'SYNTHETIC only; no native/model/service dispatched','apiSource':str(Path(__import__('hollywood_backbone.store',fromlist=['Store']).__file__))}
   (root/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
 finally:await api.close();store.close()
asyncio.run(main())
