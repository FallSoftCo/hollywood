"""Actual frozen API process SIGTERM with PG/TLS-NATS/readers/held mutation.

Fixture routing and a pre-handler barrier control timing; production package,
HTTP/SSE handler, federation startup/cleanup and SQL mutation are unmodified.
No model, native runtime, production authority or live service is involved.
"""
import asyncio,hashlib,importlib.metadata,json,os,secrets,signal,socket,subprocess,sys,time,zipfile
from pathlib import Path
from uuid import uuid4

def emit(**value):print(json.dumps(value),flush=True)
def free_port():
 with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]

async def child(spec):
 global CHILD_STORE
 sys.path.insert(0,spec['package'])
 from aiohttp import web
 from hollywood_backbone.store import Store
 from hollywood_backbone.federation_runtime import application
 from hollywood_backbone.federation_service import Client
 from hollywood_backbone import server as api
 from hollywood_backbone.server import NODE_ROUTER,FEDERATED_CATALOG,FEDERATED_MESSAGES,FEDERATED_AGENT_GATEWAY
 store=Store(spec['database']);CHILD_STORE=store;store.migrate()
 app=await application(store,spec['federation'])
 connection=app[FEDERATED_AGENT_GATEWAY].connection
 # Only the read destination/delay is a fixture. The stream() body and actual
 # TLS-NATS Client/Service call and production on_cleanup remain unchanged.
 class ReadFixture:
  node_id=spec['host']
  async def owner(self,request,kind,identity):return spec['peer']
  async def destination(self,request,action,args,body,provenance):return self.node_id
  async def call(self,request,node,action,args,**kwargs):
   assert action=='history'
   client=Client(connection,authority_id=spec['authority'],host_id=spec['host'],target_node_id=spec['peer'])
   value=await client.request('hollywood.fixture.history',{'actor':request['actor'],'room':args[0],'after':args[1]},request_id=str(uuid4()),timeout=5)
   return {'status':200,'body':value}
 for key in (FEDERATED_CATALOG,FEDERATED_MESSAGES,FEDERATED_AGENT_GATEWAY):del app[key]
 app[NODE_ROUTER]=ReadFixture()
 @web.middleware
 async def controlled_mutation(request,handler):
  if request.method=='POST':
   value=await request.json()
   if value.get('operationId')==spec['operation'] and spec['hold']:
    emit(event='mutation-held')
    release=await asyncio.to_thread(sys.stdin.buffer.read,1)
    assert release==b'R'
  return await handler(request)
 app.middlewares.append(controlled_mutation)
 async def shutdown(application):
  closing=getattr(api,'STREAM_CLOSING',None)
  present=closing is not None and closing in application
  if not spec['legacy']:assert present and application[closing].is_set()
  emit(event='shutdown-started',streamsClosingHookPresent=present)
 app.on_shutdown.append(shutdown)
 return app

async def main():
 root=Path(os.environ['API_GRACEFUL_EVIDENCE']);root.mkdir(mode=0o700)
 package=Path(os.environ['API_GRACEFUL_PACKAGE']);raw=package.read_bytes()
 package_sha=hashlib.sha256(raw).hexdigest()
 assert package_sha in {'c5aa25260595478c9aea64620cb22e649f5f30ccd6045cb6733471323fd5de40','c1de0182e69f53db63a7b05793512233b46fbdc3d2af9784a98ba116aa5cc4fb'}
 legacy=package_sha.startswith('c1de0182')
 sys.path.insert(0,str(package))
 from aiohttp import ClientSession
 from hollywood_backbone.store import Store
 from hollywood_backbone.backplane import connect_host
 from hollywood_backbone.federation_service import Service
 database=json.loads(Path(os.environ['ROOM_NATIVE_DATABASE']).read_text())
 store=Store(database);store.migrate();authority,room,host,peer=[str(uuid4()) for _ in range(4)]
 credentials={name:{'principalId':str(uuid4()),'token':secrets.token_urlsafe(40)} for name in ('Owner','Global')}
 store.bootstrap(authority,'SYNTHETIC API graceful shutdown',{'globalRoomId':room,'credentials':credentials})
 actor=credentials['Global']['principalId'];owner=credentials['Owner']['principalId']
 prelude=store.post(owner,room,'prelude',{'kind':'message','body':'Synthetic retained cursor','audience':'room'})['event'];cursor=prelude['seq']
 operation='held-mutation-'+uuid4().hex
 cert,key=root/'qa-cert.pem',root/'qa-key.pem'
 subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(key),'-out',str(cert),'-days','1','-subj','/CN=localhost','-addext','subjectAltName=DNS:localhost,IP:127.0.0.1'],check=True,capture_output=True,timeout=15)
 port=free_port();passwords={node:secrets.token_urlsafe(40) for node in (host,peer)}
 config={'host':'127.0.0.1','port':port,'max_payload':524288,'tls':{'cert_file':str(cert),'key_file':str(key)},
  'authorization':{'users':[{'user':node,'password':passwords[node]} for node in (host,peer)]}}
 broker_config=root/'broker.private.json';broker_config.write_text(json.dumps(config));broker_log=(root/'broker.log').open('wb')
 broker=subprocess.Popen([os.environ['API_GRACEFUL_NATS'],'-c',str(broker_config)],stdout=broker_log,stderr=broker_log,env={**os.environ,'GOMAXPROCS':'2'})
 connection=None;service=None;processes=[];tasks=[];release=asyncio.Event();hold=[False];held=asyncio.Event();held_count=[0];calls=[];events=[]
 async def history(request):
  value=request.payload;assert request.source_host_id==host and value['actor']==actor and value['room']==room
  calls.append(value['after'])
  if hold[0]:
   held_count[0]+=1
   if held_count[0]==4:held.set()
   await release.wait()
  return await asyncio.to_thread(store.history,actor,room,value['after'],100,None,'conversation')
 try:
  # Startup readiness only, bounded by a single deadline; never model polling.
  deadline=time.monotonic()+5
  while True:
   try:
    reader,writer=await asyncio.open_connection('127.0.0.1',port);writer.close();await writer.wait_closed();break
   except OSError:
    assert broker.poll() is None and time.monotonic()<deadline;await asyncio.sleep(.02)
  connection=await connect_host(server='tls://localhost:'+str(port),host_id=peer,password=passwords[peer],ca_file=str(cert))
  service=Service(connection,authority_id=authority,host_id=peer,handlers={'hollywood.fixture.history':history});await service.start()
  password=root/'host-password.private';password.write_text(passwords[host]);password.chmod(0o600)
  federation=root/'federation.private.json';federation.write_text(json.dumps({'authorityId':authority,'hostId':host,'servers':['tls://localhost:'+str(port)],'passwordFile':str(password),'caFile':str(cert)}))
  async def launch(label,hold_mutation):
   spec={'package':str(package),'database':database,'federation':str(federation),'authority':authority,'host':host,'peer':peer,'port':free_port(),'operation':operation,'hold':hold_mutation,'legacy':legacy}
   path=root/(label+'.private.json');path.write_text(json.dumps(spec));ready=asyncio.Event();mutation=asyncio.Event();closing=asyncio.Event()
   env={k:v for k,v in os.environ.items() if k!='PYTHONPATH'}
   err=(root/(label+'.stderr')).open('wb')
   proc=await asyncio.create_subprocess_exec(sys.executable,'-I','-B',str(Path(__file__).resolve()),'--child',str(path),stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=err,env=env)
   processes.append((proc,err))
   async def output():
    async for line in proc.stdout:
     value=json.loads(line);events.append({'process':label,**value})
     if value.get('event')=='ready':ready.set()
     elif value.get('event')=='mutation-held':mutation.set()
     elif value.get('event')=='shutdown-started':closing.set()
   tasks.append(asyncio.create_task(output()))
   await asyncio.wait_for(ready.wait(),8)
   return proc,spec,mutation,closing
  first,spec,mutation,closing=await launch('original',True)
  async with ClientSession() as session:
   url='http://127.0.0.1:'+str(spec['port'])+'/hollywood/v3/rooms/'+room
   headers={'Authorization':'Bearer '+credentials['Global']['token']}
   readers=[await session.get(url+'/events?view=conversation&after='+str(cursor),headers=headers) for _ in range(4)]
   assert all(r.status==200 for r in readers)
   for r in readers:assert await asyncio.wait_for(r.content.readline(),3)==b': heartbeat\n'
   hold[0]=True;await asyncio.wait_for(held.wait(),6)
   async def mutate():
    async with session.post(url+'/messages',headers={'Authorization':'Bearer '+credentials['Owner']['token']},json={'operationId':operation,'kind':'message','body':'SYNTHETIC one held mutation','audience':'room'}) as response:
     assert response.status==200,await response.text();return await response.json()
   pending=asyncio.create_task(mutate());await asyncio.wait_for(mutation.wait(),3)
   start=time.monotonic();first.send_signal(signal.SIGTERM);await asyncio.wait_for(closing.wait(),3)
   assert first.returncode is None
   first.stdin.write(b'R');await first.stdin.drain();release.set();hold[0]=False
   answer=await asyncio.wait_for(pending,8);event=answer['event']
   legacy_client_close=False
   if legacy:
    # Old172 has NO closing hook. Observe the actual framework, then close ONLY
    # these owned read clients normally; never cancel the mutation/server or
    # add a shutdown hook to the frozen old package merely to make a pass.
    try:await asyncio.wait_for(first.wait(),1)
    except asyncio.TimeoutError:
     legacy_client_close=True
     for response in readers:response.close()
   if not legacy_client_close:
    for response in readers:await asyncio.wait_for(response.read(),8);response.close()
   code=await asyncio.wait_for(first.wait(),min(15,20-(time.monotonic()-start)))
   elapsed=time.monotonic()-start;assert code==0 and elapsed<20
   page=store.history(actor,room,cursor,100,None,'conversation');matches=[r for r in page['events'] if r['id']==event['id']]
   assert len(matches)==1 and len(page['events'])==1
   second,next_spec,_,next_closing=await launch('resumed',False)
   resumed='http://127.0.0.1:'+str(next_spec['port'])+'/hollywood/v3/rooms/'+room+'/events?view=conversation&after='+str(cursor)
   async with session.get(resumed,headers=headers) as response:
    assert response.status==200
    while True:
     line=await asyncio.wait_for(response.content.readline(),3)
     if line.startswith(b'data: '):assert json.loads(line[6:])['id']==event['id'];break
   second.send_signal(signal.SIGTERM);await asyncio.wait_for(next_closing.wait(),3);assert await asyncio.wait_for(second.wait(),15)==0
  with zipfile.ZipFile(package) as z:server_hash=hashlib.sha256(z.read('hollywood_backbone/server.py')).hexdigest()
  result={'actualPackageSha256':hashlib.sha256(raw).hexdigest(),'serverSourceSha256':server_hash,'realHttpPgTlsNats':True,
   'concurrentReadStreams':4,'streamsInsideActualNatsReadAtSignal':held_count[0]>=4,'controlledHeldMutationCommittedExactlyOnce':True,
   'readersClosedAndOriginalCursorResumed':True,'originalCursor':cursor,'sigtermExitSeconds':round(elapsed,6),'unitDeadlineSeconds':20,
   'exitCode':code,'sigkillUsedForAcceptance':False,'productionPackageChanged':False,'newDrainEndpoint':False,'modelsOrNativeStarted':0,
   'legacy172WithoutClosingHook':legacy,'ownedReadClientQuiescenceUsed':legacy_client_close,
   'dependencyVersions':{p:importlib.metadata.version(p) for p in ('aiohttp','nats-py','psycopg','psycopg-pool')},
   'limits':['CPU isolated synthetic authority with injected timing/read-destination fixture, not live GPU quiet preflight','Actual package/framework shutdown and federation on_cleanup unmodified; no all-fleet activation claim']}
  (root/'result.json').write_text(json.dumps(result,indent=2)+'\n');emit(**result)
 finally:
  release.set()
  for proc,err in processes:
   if proc.returncode is None:
    proc.stdin.close();proc.send_signal(signal.SIGTERM)
    try:await asyncio.wait_for(proc.wait(),8)
    except asyncio.TimeoutError:
     # Failed QA cleanup only; recorded failure must never be a passing kill.
     proc.kill();await proc.wait();(root/'FAILED-FORCED-QA-CLEANUP').write_text('Fixture failed; not qualified\n')
   err.close()
  for task in tasks:await task
  if service:await service.stop()
  if connection:await connection.close()
  broker.terminate()
  try:await asyncio.to_thread(broker.wait,5)
  except subprocess.TimeoutExpired:broker.kill();broker.wait();(root/'FAILED-BROKER-CLEANUP').write_text('Not qualified\n')
  broker_log.close();store.close();(root/'events.json').write_text(json.dumps(events,indent=2)+'\n')

if __name__=='__main__':
 os.umask(0o077)
 if len(sys.argv)==3 and sys.argv[1]=='--child':
  from aiohttp import web
  spec=json.loads(Path(sys.argv[2]).read_text());CHILD_STORE=None
  # ACTUAL production run_app default shutdown path and deadline, not an
  # asyncio.run approximation that would skip its GracefulExit handling.
  try:web.run_app(child(spec),host='127.0.0.1',port=spec['port'],access_log=None,print=lambda _:emit(event='ready',port=spec['port']))
  finally:
   if CHILD_STORE:CHILD_STORE.close();emit(event='store-closed')
 else:asyncio.run(main())
