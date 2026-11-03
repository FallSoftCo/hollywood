"""Two isolated real PG stores + authenticated NATS; NO native/model calls."""
import asyncio,json,os,secrets,socket,subprocess,time,copy
from pathlib import Path
from uuid import uuid4
import psycopg,nats
from aiohttp import ClientSession,ClientTimeout
from aiohttp.test_utils import TestServer
from hollywood_backbone.server import application
from hollywood_backbone.federation_node_router import NodeRouter
from hollywood_backbone.federation_agent_gateway import LocalAgentGateway
from psycopg.types.json import Jsonb
from hollywood_backbone.store import Store,digest,Denied,Conflict
from hollywood_backbone.backplane_auth import AgentBindings
from hollywood_backbone.federation_identity import describe,install_reference,install_room_reference
from hollywood_backbone.federation_domain import AgentDomain
from hollywood_backbone.federation_service import Service,Client
from hollywood_backbone.backplane import BackplaneError
from hollywood_backbone.room_agent_home import Allocation,home_birth,validate_home_descriptor

ROOT=Path(os.environ['ROOM_HOME_NATS_QA']);U=lambda:str(uuid4())

def save(name,value):(ROOT/name).write_text(json.dumps(value,indent=2)+'\n')

def boot(config,authority,node,name):
 store=Store(config);store.migrate();store.local_node_id=node
 owner,parent,globalroom=U(),U(),U();tokens={k:secrets.token_urlsafe(40) for k in ('Owner','Global')}
 store.bootstrap(authority,name,{'globalRoomId':globalroom,'credentials':{'Owner':{'principalId':owner,'token':tokens['Owner']},'Global':{'principalId':parent,'token':tokens['Global']}}})
 with store.pool.connection() as c:c.execute('UPDATE hb_principal SET home_node=%s WHERE id=%s',(node,parent))
 store.placement(owner,node,name,'QA-only-'+node,{},parent)
 bindings=AgentBindings(store);bindings.migrate();bindings.bind(owner,host_id=node,principal_id=parent,token_digest=digest(tokens['Global']))
 return store,owner,parent,tokens,bindings

def enroll_host_reference(target,source,node):
 with source.pool.connection() as c:host=c.execute('SELECT * FROM hb_host WHERE id=%s',(node,)).fetchone()
 with target.pool.connection() as c:c.execute('INSERT INTO hb_host(id,authority,name,identity_fingerprint,capabilities) VALUES(%s,%s,%s,%s,%s)',(host['id'],host['authority'],host['name'],host['identity_fingerprint'],Jsonb(host['capabilities'])))

async def main():
 os.umask(0o077);cfg=json.loads((ROOT/'database.json').read_text())
 with psycopg.connect(**cfg,autocommit=True) as c:c.execute('CREATE DATABASE room_home')
 authority,cpu,mac=U(),U(),U();cpu_store,owner,parent,ct,cb=boot(cfg,authority,cpu,'ISOLATED CPU node')
 home_store,ho,hp,ht,hb=boot({**cfg,'dbname':'room_home'},authority,mac,'ISOLATED HOME node')
 enroll_host_reference(cpu_store,home_store,mac);enroll_host_reference(home_store,cpu_store,cpu)
 with home_store.pool.connection() as c:
  old_global_room=c.execute('SELECT home_room FROM hb_agent_role WHERE principal=%s',(hp,)).fetchone()['home_room']
  c.execute('DELETE FROM hb_global_origin WHERE agent=%s',(hp,)) # isolated original pre-upgrade shape only
 home_parent_descriptor=describe(home_store,ho,hp);cpu_parent_descriptor=describe(cpu_store,owner,parent)
 install_reference(cpu_store,owner,home_parent_descriptor,enroll_execution_reference=True)
 install_reference(home_store,ho,cpu_parent_descriptor,enroll_execution_reference=True)
 cpu_store.room_default_set(owner,cpu,{'agentId':parent,'enabled':True,'revision':0})
 cpu_store.room_default_set(owner,mac,{'agentId':hp,'enabled':True,'revision':0})
 room=U();cpu_store.create_room(owner,room,'ISOLATED authoritative home origin','Only protocol QA metadata, no business input',parent)
 # Real broker with separate authenticated users and source-subject ACLs. No
 # production NATS URL/account/profile is read or contacted; plaintext loopback
 # is a fixture limit, not TLS qualification.
 users=[];passwords={}
 for name,node in [('cpu',cpu),('home',mac)]:
  password=secrets.token_urlsafe(40);passwords[name]=password
  users.append({'user':name,'password':password,'permissions':{'publish':{'allow':[f'hollywood.v2.{authority}.node.*.service.{node}.>','_INBOX.>','$SRV.>']},'subscribe':{'allow':[f'hollywood.v2.{authority}.node.{node}.service.>','_INBOX.>','$SRV.>']}}})
 sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
 config={'host':'127.0.0.1','port':port,'authorization':{'users':users},'pid_file':str(ROOT/'nats.pid'),'server_name':'isolated-room-home-qa'}
 (ROOT/'nats.conf').write_text(json.dumps(config));log=(ROOT/'nats.log').open('w');broker=subprocess.Popen(['/opt/hollywood-node-cpu-20261029-r01/nats-server','-c',str(ROOT/'nats.conf')],stdout=log,stderr=log)
 save('broker-intent.json',{'pid':broker.pid,'port':port,'privateLoopbackOnly':True,'productionSubjects':False})
 cc=hc=None;services=[];apis=[];writes=[];lost=[False];errors=[];permission=asyncio.Event();settled=False
 async def on_error(error):
  errors.append(type(error).__name__+':'+str(error))
  if 'permission' in str(error).lower():permission.set()
 try:
  deadline=time.monotonic()+5
  while 'Server is ready' not in (ROOT/'nats.log').read_text():
   if broker.poll() is not None or time.monotonic()>deadline:raise RuntimeError('Private broker failed before protocol dispatch')
   await asyncio.sleep(.02)
  cc=await nats.connect(f'nats://127.0.0.1:{port}',user='cpu',password=passwords['cpu'],error_cb=on_error)
  hc=await nats.connect(f'nats://127.0.0.1:{port}',user='home',password=passwords['home'],error_cb=on_error)
  cpu_domain=AgentDomain(cb);home_domain=AgentDomain(hb)
  async def receiver(req):
   action=req.payload.get('action')
   if action=='room_agent_home_attach':writes.append({'id':req.request_id,'args':req.payload['args']})
   value=await cpu_domain.invoke(req)
   if action=='room_agent_home_attach' and value['status']==200 and not lost[0]:
    lost[0]=True;raise BackplaneError('REPLY_UNKNOWN') # AFTER real SQL commit
   return value
  for connection,node,domain,handler in [(cc,cpu,cpu_domain,receiver),(hc,mac,home_domain,home_domain.invoke)]:
   service=Service(connection,authority_id=authority,host_id=node,node_id=node,legacy=False,
       handlers={'hollywood.agent.domain':handler,'hollywood.agent.authenticate':domain.authenticate})
   await service.start();services.append(service)
  client=Client(hc,authority_id=authority,host_id=mac,target_node_id=cpu)
  base={'actorId':hp,'credentialDigest':digest(ht['Global']),'homeNodeId':mac,'homeRevision':home_parent_descriptor['principal']['home_revision'],'homeRoleRevision':home_parent_descriptor['role']['revision'],'nativeSource':None}
  async def call(action,args,body=None):
   return await client.request('hollywood.agent.domain',{**base,'action':action,'verb':'POST' if action in ('room_agent_home_attach','room_agents_initialize','global_origin_attach') else 'GET','path':'rooms/'+room+'/environment-agents/'+mac,'body':body,'args':args},request_id=U())
  # Actual local HTTP applications on BOTH nodes; no fake request/router.
  cpu_gateway=LocalAgentGateway(cc,authority_id=authority,host_id=cpu,node_id=cpu)
  home_gateway=LocalAgentGateway(hc,authority_id=authority,host_id=mac,node_id=mac)
  cpu_router=NodeRouter(cpu_store,cpu_gateway);home_router=NodeRouter(home_store,home_gateway)
  for store,gateway,router in [(cpu_store,cpu_gateway,cpu_router),(home_store,home_gateway,home_router)]:
   api=TestServer(application(store,node_router=router,federated_agent_gateway=gateway));await api.start_server();apis.append(api)
  async with ClientSession(timeout=ClientTimeout(total=30)) as session:
   async def http(api,method,path,body=None,token=None):
    async with session.request(method,api.make_url('/hollywood/v3/'+path),json=body,
        headers={'Authorization':'Bearer '+(token or ht['Global'])}) as response:
     return response.status,await response.json()
   # The original public input predates identity/native readiness. Its immutable
   # intended recipients are already reserved, never old Global fan-out.
   original=cpu_store.post(owner,room,U(),{'kind':'request','body':'ISOLATED pending recipient input','audience':'room'})['event']
   agent=next(r['agent'] for r in cpu_store.room_agents_read(owner,room)['agents'] if r['node']==mac)
   assert agent in original['data']['conversationRecipients'] and hp not in original['data']['conversationRecipients']
   st,catalog=await http(apis[1],'GET','rooms');assert st==200,(st,catalog)
   assert any(p['room']==room and p['ownerNodeId']==cpu for p in catalog['roomAgentInitializations'])
   with home_store.pool.connection() as c:
    route=c.execute("SELECT * FROM hb_federation_resource_route WHERE kind='room' AND resource=%s",(room,)).fetchone()
    assert str(route['owner_node'])==cpu # desired-only discovery, no room membership needed
   path='rooms/'+room+'/environment-agents'
   st,init=await http(apis[1],'POST',path,{'operationId':'original-http-initialize'})
   assert st==200 and init['controlOnly'] and len(init['agents'])==1,(st,init)
   assert cpu_store.principal_read(owner,hp)['administrator'] is False
   st,init_receipt=await http(apis[1],'GET','administration/commands/original-http-initialize')
   assert st==200 and init_receipt['acceptedResult']==init,(st,init_receipt)
   token=secrets.token_urlsafe(40);operation='original-http-home-enroll';enroll_path=path+'/'+mac+'/enroll'
   # Definite HOME-only boundary: CPU HTTP does not forward the bearer at all.
   st,denied=await http(apis[0],'POST',enroll_path,{'operationId':'must-not-forward','enrollmentCredential':token},ct['Global'])
   assert st==403 and not writes,(st,denied,writes)
   st,unknown=await http(apis[1],'POST',enroll_path,{'operationId':operation,'enrollmentCredential':token})
   assert st==503 and unknown['mayApply'] is True,(st,unknown)
   assert len(writes)==1 # fixture loses reply AFTER canonical SQL commit
   st,receipt=await http(apis[1],'GET','administration/commands/'+operation)
   assert st==200 and receipt['acceptedResult']['referenceOnly'],(st,receipt)
   assert receipt['wireDigest']==digest({'method':'POST','path':enroll_path,'body':{'operationId':operation,'enrollmentCredential':token}})
   st,repeat=await http(apis[1],'POST',enroll_path,{'operationId':operation,'enrollmentCredential':token})
   assert st==200 and repeat==receipt['acceptedResult'] and len(writes)==1
   # Reopen actual ingress/router from unchanged PG; all reconciliation remains
   # source-bound and read only. No new birth/start/attachment on restart.
   await apis[1].close()
   home_router=NodeRouter(home_store,home_gateway)
   apis[1]=TestServer(application(home_store,node_router=home_router,federated_agent_gateway=home_gateway));await apis[1].start_server()
   st,reopened=await http(apis[1],'GET','administration/commands/'+operation)
   assert st==200 and reopened==receipt and len(writes)==1
   st,changed=await http(apis[1],'POST',enroll_path,{'operationId':operation,'enrollmentCredential':secrets.token_urlsafe(40)})
   assert st==409 and len(writes)==1,(st,changed)
   assert home_store.authenticate(token)['id']==agent
   try:cpu_store.authenticate(token)
   except Denied:pass
   else:raise AssertionError('Receiver authenticated HOME bearer')
   with cpu_store.pool.connection() as c:
    assert c.execute('SELECT count(*) AS n FROM hb_token WHERE principal=%s',(agent,)).fetchone()['n']==0
    received=c.execute('SELECT * FROM hb_principal WHERE id=%s',(agent,)).fetchone()
    assert received['identity_reference'] and str(received['home_node'])==mac and not received['administrator']
   with home_store.pool.connection() as c:
    assert c.execute('SELECT count(*) AS n FROM hb_room_agent_home_birth WHERE agent=%s',(agent,)).fetchone()['n']==1
    outbound=c.execute('SELECT * FROM hb_room_agent_home_outbound WHERE operation=%s',(operation,)).fetchone()
    assert token not in json.dumps(outbound,default=str) and outbound['phase']=='answered'
   st,role=await http(apis[1],'GET','agents/'+agent+'/role')
   assert st==200 and role['home_room']==room and role['primary_role']=='room-agent'
   st,history=await http(apis[1],'GET','rooms/'+room+'/history?view=conversation&before=9007199254740991&limit=10',token=token)
   assert st==200,(st,history)
   events=history['events']
   assert [e['id'] for e in events if e['kind']=='request']==[original['id']]
   # Exact home report is scoped host-control metadata; NONADMIN receiving
   # reference does not gain room initialization or arbitrary administration.
   st,current=await http(apis[1],'GET',path);assert st==200
   slot=current['agents'][0]
   st,reported=await http(apis[1],'POST',path+'/'+mac+'/report',{'operationId':'original-http-report','revision':slot['revision'],'state':'blocked','note':'QA no native calls authorized','receipt':None})
   assert st==200 and reported['controlOnly'] and len(reported['agents'])==1,(st,reported)
   st,report_receipt=await http(apis[1],'GET','administration/commands/original-http-report')
   assert st==200 and report_receipt['acceptedResult']==reported
   # Same source cannot initialize an unrelated canonical room with no slot.
   other=U()
   with cpu_store.pool.connection() as c:
    c.execute("INSERT INTO hb_room(id,authority,name,purpose,owner) VALUES(%s,%s,'ISOLATED unrelated','no scoped allocation',%s)",(other,authority,parent))
   st,blocked=await http(apis[1],'POST','rooms/'+other+'/environment-agents',{'operationId':'no-scope'})
   assert st==503 and blocked['mayApply'] is False,(st,blocked)
   denied=await call('room_agents_initialize',[other],{'operationId':'no-scope'})
   assert denied['status']==403,denied
   # Review an EXACT legacy room-ID manifest. New recipients remain STAGED;
   # original queued/historical recipients and current Global role are untouched.
   legacy=U()
   with cpu_store.pool.connection() as c:
    c.execute("INSERT INTO hb_room(id,authority,name,purpose,owner) VALUES(%s,%s,'ISOLATED explicit legacy ID','reviewed production-like scope',%s)",(legacy,authority,parent))
    c.execute('INSERT INTO hb_membership(authority,room,principal,can_read,can_post,can_request) VALUES(%s,%s,%s,true,true,true)',(authority,legacy,parent))
   cpu_store.membership(owner,legacy,hp,{'can_read':True,'can_post':True,'can_request':True},0)
   old_input=cpu_store.post(owner,legacy,U(),{'kind':'request','body':'Original work must remain with original recipients','audience':'room'})['event']
   old_private=cpu_store.post(owner,legacy,U(),{'kind':'request','body':'Original private source','audience':'direct','recipientId':hp})['event']

   old_role=cpu_store.role_read(owner,parent)
   manifest={'kind':'project','roomRevision':1,'ownerId':parent,'priorKind':'legacy',
    'environments':[{'node':cpu,'coordinator':parent},{'node':mac,'coordinator':hp}],'reviewSha256':'a'*64,
    'purpose':'Room-owned environment contributors; preserve the original business prohibitions. No historic replay.',
    'priorPurposeDigest':digest('reviewed production-like scope')}
   st,missing=await http(apis[0],'POST','rooms/'+legacy+'/origin-adoption',{'operationId':'must-cover-all-registered-homes','manifest':{**manifest,'environments':manifest['environments'][:1]}},ct['Global'])
   assert st==409,(st,missing)
   st,boolean=await http(apis[0],'POST','rooms/'+legacy+'/origin-adoption',{'operationId':'strict-revision-not-boolean','manifest':{**manifest,'roomRevision':True}},ct['Global'])
   assert st==400,(st,boolean)
   st,adopted=await http(apis[0],'POST','rooms/'+legacy+'/origin-adoption',{'operationId':'legacy-project-adopt','manifest':manifest},ct['Global'])
   assert st==200 and adopted['phase']=='staging' and adopted['originalRecipientsChanged'] is False,(st,adopted)
   st,repeated=await http(apis[0],'POST','rooms/'+legacy+'/origin-adoption',{'operationId':'legacy-project-adopt','manifest':manifest},ct['Global'])
   assert st==200 and repeated==adopted
   with cpu_store.pool.connection() as c:
    saved=c.execute('SELECT data FROM hb_event WHERE id=%s',(old_input['id'],)).fetchone()['data']
    assert saved['conversationRecipients']==old_input['data']['conversationRecipients']
   assert cpu_store.role_read(owner,parent)==old_role
   with cpu_store.pool.connection() as c:
    adoption=c.execute('SELECT * FROM hb_room_origin_adoption WHERE room=%s',(legacy,)).fetchone()
    current_purpose=c.execute('SELECT purpose FROM hb_room WHERE id=%s',(legacy,)).fetchone()['purpose']
    assert adoption['prior_room']['purpose']=='reviewed production-like scope' and current_purpose==manifest['purpose']
   after=cpu_store.post(owner,legacy,U(),{'kind':'request','body':'Still old fleet until real handoff','audience':'room'})['event']
   assert after['data']['conversationRecipients']==old_input['data']['conversationRecipients']
   st,rejected=await http(apis[0],'POST','rooms/'+legacy+'/origin-adoption',{'operationId':'changed-manifest-no-rewrite','manifest':{**manifest,'reviewSha256':'b'*64}},ct['Global'])
   assert st==409,(st,rejected)
   # REAL nonadmin HOME reference after future-recipient handoff. The native
   # observations below are explicitly SYNTHETIC metadata, not another native
   # proof or a claim that model threads were started here.
   lp='rooms/'+legacy+'/environment-agents';lt={cpu:secrets.token_urlsafe(40),mac:secrets.token_urlsafe(40)};children={}
   for node,index,parent_token in [(cpu,0,ct['Global']),(mac,1,ht['Global'])]:
    st,born=await http(apis[index],'POST',lp+'/'+node+'/enroll',{'operationId':'legacy-enroll-'+node,'enrollmentCredential':lt[node]},parent_token)
    assert st==200,(st,born);children[node]=born['agentId']
    st,plan=await http(apis[index],'GET',lp,token=parent_token);assert st==200,(st,plan)
    slot=next(i for i in plan['agents'] if i['node']==node)
    evidence={'nativeThreadId':'SYNTHETIC-'+born['agentId'],'nativeHostId':node,'appServerManifestSha256':'e'*64,'sharedAppServer':True}
    st,report=await http(apis[index],'POST',lp+'/'+node+'/report',{'operationId':'legacy-report-'+node,'revision':slot['revision'],'state':'provisioning','note':'Synthetic metadata fixture; no native calls','receipt':evidence},parent_token)
    assert st==200,(st,report)
    st,observation=await http(apis[index],'POST','rooms/'+legacy+'/activity',{'observationType':'nativeState','nativeThread':evidence['nativeThreadId'],'revision':1,'status':{'type':'idle','activeFlags':[]}},lt[node])
    assert st==200,(st,observation)
   st,old_observation=await http(apis[1],'POST','rooms/'+legacy+'/activity',{'observationType':'nativeState','nativeThread':'SYNTHETIC-retained-global','revision':1,'status':{'type':'activeElsewhere','activeFlags':[]}})
   assert st==200,(st,old_observation)
   accepted_body={'operationId':'old-accepted-before-handoff','kind':'message','audience':'room','body':'Original progress','sourceEventId':old_input['id'],'responsePhase':'update'}
   st,old_publication=await http(apis[1],'POST','rooms/'+legacy+'/messages',accepted_body);assert st==200,(st,old_publication)
   plan=cpu_store.room_agents_read(owner,legacy)
   retirement={'operationId':'legacy-handoff','revision':plan['generation']['revision'],'retiringAgentIds':[parent,hp],
    'handoff':{'originalWorkDisposition':'retained-with-original-owners','ownerReceiptSha256':'f'*64}}
   st,activated=await http(apis[0],'POST',lp+'/activate',retirement,ct['Global']);assert st==200,(st,activated)
   with cpu_store.pool.connection() as c:
    member=c.execute('SELECT * FROM hb_membership WHERE room=%s AND principal=%s',(legacy,hp)).fetchone()
    assert member['can_read'] and not member['can_post'] and not member['can_request']
   assert cpu_store.principal_read(owner,hp)['administrator'] is False
   detail=cpu_store.room_detail(owner,legacy)
   assert detail['nativeState']['runtimeCount']==2 and {r['agentId'] for r in detail['nativeState']['agents']}==set(children.values()),detail['nativeState']
   # Old accepted operation read/repeat must remain exact, with ZERO new event.
   st,reconciled=await http(apis[1],'POST','rooms/'+legacy+'/messages',accepted_body)
   assert st==200 and reconciled==old_publication,(st,reconciled)
   st,command=await http(apis[1],'GET','commands/old-accepted-before-handoff')
   assert st==200 and command['event']==old_publication['event'],(st,command)
   st,public_answer=await http(apis[1],'POST','rooms/'+legacy+'/messages',{'operationId':'retained-public-answer','kind':'message','audience':'room','body':'Original public outcome','sourceEventId':old_input['id'],'responsePhase':'answer'})
   assert st==200 and public_answer['event']['source']==old_input['id'],(st,public_answer)
   st,private_answer=await http(apis[1],'POST','rooms/'+legacy+'/messages',{'operationId':'retained-private-answer','kind':'message','audience':'direct','recipientId':owner,'body':'Original private outcome','sourceEventId':old_private['id'],'responsePhase':'answer'})
   assert st==200 and private_answer['event']['audience']=='direct' and private_answer['event']['recipient']==owner,(st,private_answer)
   new_input=cpu_store.post(owner,legacy,U(),{'kind':'request','body':'New generation input','audience':'room'})['event']
   assert hp not in new_input['data']['conversationRecipients']
   negatives=[
    {'operationId':'no-new-source','kind':'message','audience':'room','body':'Must not publish','sourceEventId':new_input['id'],'responsePhase':'answer'},
    {'operationId':'no-new-task','kind':'request','audience':'room','recipientId':parent,'body':'Must not delegate','sourceEventId':old_input['id']},
    {'operationId':'no-unattributed-post','kind':'message','audience':'room','body':'Must not publish'},
    {'operationId':'no-private-expansion','kind':'message','audience':'room','body':'Must not leak','sourceEventId':old_private['id'],'responsePhase':'answer'}]
   for denied in negatives:
    st,rejection=await http(apis[1],'POST','rooms/'+legacy+'/messages',denied);assert st==403,(denied['operationId'],st,rejection)
   # Genuinely active OLD source may appear, but not an unrelated shared thread.
   st,own_active=await http(apis[1],'POST','rooms/'+legacy+'/activity',{'observationType':'nativeState','nativeThread':'SYNTHETIC-retained-global','revision':2,'sourceEventId':old_input['id'],'status':{'type':'active','activeFlags':[]}})
   assert st==200,(st,own_active)
   assert cpu_store.room_detail(owner,legacy)['nativeState']['runtimeCount']==3
   with cpu_store.pool.connection() as c:c.execute("UPDATE hb_conversation_state SET observed_at=now()-interval '10 minutes' WHERE room=%s AND agent=%s",(legacy,hp))
   assert cpu_store.room_detail(owner,legacy)['nativeState']['runtimeCount']==2 # expired old work excluded, not shown unavailable
   st,settled=await http(apis[1],'POST','rooms/'+legacy+'/activity',{'observationType':'nativeState','nativeThread':'SYNTHETIC-retained-global','revision':3,'status':{'type':'idle','activeFlags':[]}})
   assert st==200,(st,settled)
   assert cpu_store.room_detail(owner,legacy)['nativeState']['runtimeCount']==2
   cat=cpu_store.catalog(owner);project=next(r for r in cat['rooms'] if r['id']==legacy)
   assert project['nativeState']['runtimeCount']==2
   st,not_owned=await http(apis[1],'POST','rooms/'+legacy+'/activity',{'observationType':'nativeState','nativeThread':'SYNTHETIC-retained-global','revision':4,'sourceEventId':new_input['id'],'status':{'type':'active','activeFlags':[]}})
   assert st==403,(st,not_owned)
   # Canonical legacy Global kind adoption is explicit too, never name-based.
   with cpu_store.pool.connection() as c:
    global_room=str(c.execute('SELECT home_room FROM hb_agent_role WHERE principal=%s',(parent,)).fetchone()['home_room'])
    c.execute("UPDATE hb_room SET room_kind='legacy' WHERE id=%s",(global_room,)) # isolated old server shape
   with cpu_store.pool.connection() as c:
    old_global_metadata=json.loads(json.dumps(c.execute('SELECT * FROM hb_room WHERE id=%s',(global_room,)).fetchone(),default=str))
   install_room_reference(home_store,hp,old_global_metadata,owner_node=cpu)
   global_manifest={'kind':'global','roomRevision':1,'ownerId':parent,'priorKind':'legacy','environments':[],'reviewSha256':'c'*64}
   st,typed=await http(apis[0],'POST','rooms/'+global_room+'/origin-adoption',{'operationId':'typed-global-adopt','manifest':global_manifest},ct['Global'])
   assert st==200 and typed['roomKind']=='global'
   cpu_store.membership(owner,global_room,hp,{'can_read':True,'can_post':True,'can_request':True},0)
   global_detail=await call('room_detail',[global_room]);assert global_detail['status']==200,global_detail
   # Original authenticated canonical response supplies room metadata, NOT a
   # CPU-minted home identity. Install only its foreign-key/typed route reference.
   canonical=global_detail['body']['room']
   # Existing old reference is deliberately NOT refreshed by the fixture;
   # production HTTP authenticates the actual canonical response before adoption.
   with home_store.pool.connection() as c:
    prior_role=json.loads(json.dumps(c.execute('SELECT * FROM hb_agent_role WHERE principal=%s',(hp,)).fetchone(),default=str))
   gm={'roomId':global_room,'roomRevision':typed['revision'],'ownerId':parent,'homeNodeId':mac,
       'homeRevision':1,'roleRevision':prior_role['revision'],'priorHomeRoomId':prior_role['home_room'],'reviewSha256':'d'*64}
   st,declared=await http(apis[1],'POST','agents/'+hp+'/origin-adoption',{'operationId':'global-home-origin-adopt','manifest':gm})
   assert st==200 and declared['kind']=='reviewed-adoption' and declared['prior_role']==prior_role,(st,declared)
   assert home_store.role_read(hp,hp)==prior_role # no home_room/context rewrite
   st,described=await http(apis[1],'GET','agents/'+hp+'/origin-adoption')
   assert st==200 and described==declared
   received=await call('global_origin_attach',['global-origin-reference',declared],{'operationId':'global-origin-reference'})
   assert received['status']==200 and received['body']['originalRoleUnchanged'],received
   accepted=await call('global_origin_reference_read',['global-origin-reference'])
   assert accepted['status']==200 and accepted['body']==received['body']
   detail=cpu_store.room_detail(owner,global_room)
   actor=next(a for a in detail['actors'] if a['id']==hp)
   assert actor['identity']['originRoomId']==global_room and actor['identity']['originKind']=='reviewed-global-adoption'
   assert cpu_store.principal_read(owner,hp)['administrator'] is False
   # Local canonical CPU enrollment also binds its new child to the existing
   # home transport in the same SQL transaction, no scheduler/service creation.
   local_token=secrets.token_urlsafe(40)
   st,local_child=await http(apis[0],'POST',path+'/'+cpu+'/enroll',{'operationId':'cpu-local-enroll','enrollmentCredential':local_token},ct['Global'])
   assert st==200,(st,local_child)
   local_client=Client(cc,authority_id=authority,host_id=cpu,target_node_id=cpu)
   local_auth=await local_client.request('hollywood.agent.authenticate',{'actorId':local_child['agentId'],'credentialDigest':digest(local_token)},request_id=U())
   assert local_auth['id']==local_child['agentId']
   # Canonical lost-ACK attach payload contains descriptor DIGEST only.
   assert token not in json.dumps(writes)
  descriptor=home_birth(home_store,hp,Allocation(cpu,outbound['allocation']),mac,token,operation)
  attach=operation
  try:home_birth(home_store,hp,Allocation(cpu,outbound['allocation']),mac,secrets.token_urlsafe(40),operation)
  except Conflict:pass
  else:raise AssertionError('Changed original home birth credential admitted')
  try:home_birth(cpu_store,hp,Allocation(cpu,outbound['allocation']),mac,token,operation)
  except Denied:pass
  else:raise AssertionError('Foreign raw credential birth admitted at CPU')
  wrong=await client.request('hollywood.agent.domain',{**base,'actorId':agent,'credentialDigest':'0'*64,'homeRevision':1,'homeRoleRevision':1,'action':'room_detail','verb':'GET','path':'rooms/'+room,'body':None,'args':[room]},request_id=U())
  assert wrong['status']==403,wrong
  await hc.publish(f'hollywood.v2.{authority}.node.{cpu}.service.{cpu}.hollywood.agent.domain',b'{}')
  await hc.flush();await asyncio.wait_for(permission.wait(),2)
  # Canonical revocation remains authoritative; a different attachment command
  # cannot re-enable the original grant after it was explicitly disabled.
  with cpu_store.pool.connection() as c:c.execute('UPDATE hb_federated_agent_grant SET enabled=false,revision=revision+1 WHERE principal=%s',(agent,))
  rejected=await call('room_agent_home_attach',[room,mac,'new-command-must-not-repair',descriptor],{'operationId':'new-command-must-not-repair'});assert rejected['status']==409,rejected
  result={'nonadminRetainedPublicPrivateOutcome':True,'oldAcceptedCommandUnchangedAfterHandoff':True,'newSourceTaskAndAudienceExpansionDenied':True,'currentNativeProjectionExcludesRetiredIdleAndSharedBusy':True,'genuineOldSourceActiveAndTerminalObservation':True,'nativeObservationFixture':'SYNTHETIC metadata only; zero native calls','explicitLegacyProjectStaging':True,'originalRoleAndRecipientSnapshotsUnchanged':True,'reviewedGlobalOriginNotRewrittenBirth':True,'authenticatedGlobalReferenceAndOriginalReceipt':True,'actualHttpIngressAndRestart':True,'httpLostAckReadOnlyReconciliation':True,'desiredOnlyOwnerRouting':True,'homeOnlyRawBearer':True,'scopedNonadminInitializeReportReceipts':True,'pendingOriginalInputRetainedOnce':True,'localChildTransportBinding':True,'passed':True,'realPgStores':2,'realAuthenticatedNatsNodes':2,'brokerSourceSubjectSpoofDenied':True,'homeOnlyRawCredentialAndTransportAuthentication':True,'cpuForeignCredentialRows':0,'receivingReferenceNonadminImmutableOrigin':True,'lostAckAfterCanonicalCommitReconciledReadOnly':True,'originalLostReplyAttachmentDispatches':1,'homeAttachmentCallsIncludingRevocationNegative':len(writes),'changedOriginalHomeCredentialDenied':True,'foreignHomeBirthAtCpuDenied':True,'badChildCredentialDenied':True,'revokedGrantNotRepaired':True,'nativeModelBusinessCalls':0,'productionEffects':0,'limits':['Two isolated logical nodes/databases on CPU, not actual fleet host keys or physical Mac/Windows.','Plaintext private loopback broker; production TLS policy unchanged/not certified.','Actual HTTP/home-NATS identity/receipt journey; platform/native provisioning is the separate retained CPU proof, not rerun here.']}
  save('result.json',result);settled=True;print(json.dumps(result))
 finally:
  for api in apis:await api.close()
  for service in services:await service.stop()
  for connection in [cc,hc]:
   if connection:await connection.close()
  broker.terminate()
  try:broker.wait(timeout=5)
  except subprocess.TimeoutExpired:raise RuntimeError('Exact owned QA broker did not stop; inspect original')
  log.close();cpu_store.close();home_store.close()
  save('broker-closure.json',{'settled':settled,'brokerPid':broker.pid,'brokerExit':broker.returncode,'servicesAndClientsClosed':True})
asyncio.run(main())
