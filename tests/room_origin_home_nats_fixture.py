"""Two isolated real PG stores + authenticated NATS; NO native/model calls."""
import asyncio,json,os,secrets,socket,subprocess,time,copy
from pathlib import Path
from uuid import uuid4
import psycopg,nats
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
 cc=hc=None;services=[];writes=[];lost=[False];errors=[];permission=asyncio.Event();settled=False
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
   if action=='room_agent_home_attach':writes.append(req.request_id)
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
   return await client.request('hollywood.agent.domain',{**base,'action':action,'verb':'POST' if action=='room_agent_home_attach' else 'GET','path':'rooms/'+room+'/environment-agents/'+mac,'body':body,'args':args},request_id=U())
  allocation=await call('room_agent_allocation_read',[room,mac]);assert allocation['status']==200,allocation
  proof=allocation['body'];assert proof['ownerNodeId']==cpu and proof['coordinator']==hp
  # The actual authenticated home coordinator remains NON-admin at receiver.
  assert cpu_store.principal_read(owner,hp)['administrator'] is False
  install_room_reference(home_store,hp,proof['room'],owner_node=cpu)
  token=secrets.token_urlsafe(40);operation='original-home-birth-r01'
  descriptor=home_birth(home_store,hp,Allocation(cpu,proof),mac,token,operation)
  assert home_birth(home_store,hp,Allocation(cpu,proof),mac,token,operation)==descriptor
  try:home_birth(home_store,hp,Allocation(cpu,proof),mac,secrets.token_urlsafe(40),operation)
  except Conflict:pass
  else:raise AssertionError('Changed original home credential accepted')
  try:home_birth(cpu_store,hp,Allocation(cpu,proof),mac,token,operation)
  except Denied:pass
  else:raise AssertionError('CPU minted a foreign home credential')
  validate_home_descriptor(home_store,hp,descriptor)
  agent=descriptor['identity']['principal']['id'];assert token not in json.dumps(descriptor)
  assert home_store.authenticate(token)['id']==agent
  with cpu_store.pool.connection() as c:assert c.execute('SELECT count(*) AS n FROM hb_token WHERE principal=%s',(agent,)).fetchone()['n']==0
  attach='original-reference-attach-r01'
  try:await call('room_agent_home_attach',[room,mac,attach,descriptor],{'operationId':attach})
  except BackplaneError as e:assert e.code=='REPLY_UNKNOWN'
  else:raise AssertionError('Controlled original reply loss not exercised')
  acceptance=await call('room_agent_home_attachment_read',[room,mac,attach]);assert acceptance['status']==200,acceptance
  assert acceptance['body']['referenceOnly'] and not acceptance['body']['activeLocalCredential'] and not acceptance['body']['nativeProvisioned']
  assert len(writes)==1 # Lost ACK reconciled READ ONLY, never another attach POST.
  assert home_store.authenticate(token)['id']==agent
  try:cpu_store.authenticate(token)
  except Denied:pass
  else:raise AssertionError('Receiving CPU accepted a foreign home credential')
  with cpu_store.pool.connection() as c:
   assert c.execute('SELECT count(*) AS n FROM hb_token WHERE principal=%s',(agent,)).fetchone()['n']==0
   received=c.execute('SELECT * FROM hb_principal WHERE id=%s',(agent,)).fetchone();assert received['identity_reference'] and str(received['home_node'])==mac and received['administrator'] is False
  detail=cpu_store.room_detail(owner,room);entry=next(a for a in detail['actors'] if a['id']==agent)
  assert entry['identity']['originRoomId']==room and entry['identity']['host']['id']==mac
  assert not detail['roomAgents']['complete']
  # Home-only transport authentication, not a shadow credential on receiver.
  home_client=Client(hc,authority_id=authority,host_id=mac,target_node_id=mac)
  authenticated=await home_client.request('hollywood.agent.authenticate',{'actorId':agent,'credentialDigest':digest(token)},request_id=U())
  assert authenticated['id']==agent
  try:await client.request('hollywood.agent.authenticate',{'actorId':agent,'credentialDigest':digest(token)},request_id=U())
  except BackplaneError as e:assert e.code=='AGENT_ACCESS_DENIED'
  else:raise AssertionError('Reference authenticated as authoritative at receiver')
  # Exercise original reference admission with the new child and stale/foreign
  # credentials, without native inputs, publication or a model.
  child={'actorId':agent,'credentialDigest':digest(token),'homeNodeId':mac,'homeRevision':1,'homeRoleRevision':1,'nativeSource':None,'action':'room_detail','verb':'GET','path':'rooms/'+room,'body':None,'args':[room]}
  value=await client.request('hollywood.agent.domain',child,request_id=U());assert value['status']==200
  wrong=await client.request('hollywood.agent.domain',{**child,'credentialDigest':'0'*64},request_id=U());assert wrong['status']==403
  await hc.publish(f'hollywood.v2.{authority}.node.{cpu}.service.{cpu}.hollywood.agent.domain',b'{}')
  await hc.flush();await asyncio.wait_for(permission.wait(),2)
  # Canonical revocation remains authoritative; a different attachment command
  # cannot re-enable the original grant after it was explicitly disabled.
  with cpu_store.pool.connection() as c:c.execute('UPDATE hb_federated_agent_grant SET enabled=false,revision=revision+1 WHERE principal=%s',(agent,))
  rejected=await call('room_agent_home_attach',[room,mac,'new-command-must-not-repair',descriptor],{'operationId':'new-command-must-not-repair'});assert rejected['status']==409,rejected
  result={'passed':True,'realPgStores':2,'realAuthenticatedNatsNodes':2,'brokerSourceSubjectSpoofDenied':True,'homeOnlyRawCredentialAndTransportAuthentication':True,'cpuForeignCredentialRows':0,'receivingReferenceNonadminImmutableOrigin':True,'lostAckAfterCanonicalCommitReconciledReadOnly':True,'attachWritesBeforeRevocationNegative':1,'changedOriginalHomeCredentialDenied':True,'foreignHomeBirthAtCpuDenied':True,'badChildCredentialDenied':True,'revokedGrantNotRepaired':True,'nativeModelBusinessCalls':0,'productionEffects':0,'limits':['Two isolated logical nodes/databases on CPU, not actual fleet host keys or physical Mac/Windows.','Plaintext private loopback broker; production TLS policy unchanged/not certified.','Authoritative primitive + reference transport proof; automatic local HTTP workflow bridge still pending.']}
  save('result.json',result);settled=True;print(json.dumps(result))
 finally:
  for service in services:await service.stop()
  for connection in [cc,hc]:
   if connection:await connection.close()
  broker.terminate()
  try:broker.wait(timeout=5)
  except subprocess.TimeoutExpired:raise RuntimeError('Exact owned QA broker did not stop; inspect original')
  log.close();cpu_store.close();home_store.close()
  save('broker-closure.json',{'settled':settled,'brokerPid':broker.pid,'brokerExit':broker.returncode,'servicesAndClientsClosed':True})
asyncio.run(main())
