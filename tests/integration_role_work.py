"""Real authenticated HTTP/PostgreSQL substrate checks; synthetic native-turn facts.

All fresh namespace writes roll back. No model or execution is claimed here;
the separate r242 live journey qualifies behavior and actual native admission.
"""
import asyncio,contextlib,hashlib,json,secrets,time
from pathlib import Path
from uuid import uuid4
from aiohttp import ClientSession
from aiohttp.test_utils import TestServer
from psycopg.types.json import Jsonb
from hollywood_backbone.store import Store,digest
from hollywood_backbone.server import application
from hollywood_backbone import context_system,context_room_observations,context_role_work

cfg=json.loads(Path('/var/lib/harness-deployments/ozzz-native-20261019-r01/database.json').read_text())
store=Store(cfg)
async def check(c):
 aid,gid,hid,room,host=[str(uuid4()) for _ in range(5)]
 tokens={p:secrets.token_urlsafe(32) for p in ['reader','writer','responder']}
 owner=secrets.token_urlsafe(32);globaltoken=secrets.token_urlsafe(32)
 plan={'authorityId':aid,'globalRoomId':room,'credentials':{'Owner':{'principalId':hid,'token':owner},'Global':{'principalId':gid,'token':globaltoken}}}
 class Borrow:
  @contextlib.contextmanager
  def connection(self):
   with c.transaction():yield c
 original=store.pool;store.pool=Borrow();checks={}
 def mark(name,condition):
  assert condition,name;checks[name]=True
 try:
  store.bootstrap(aid,'Rollback-only role substrate',plan)
  store.placement(hid,host,'Frozen native-turn fixture','fixture',{},gid)
  context_system.configure(store,hid,gid,{'hostId':host,'workspaceId':str(uuid4()),'bindings':[{'purpose':p,'contextId':str(uuid4()),'credentialDigest':digest(t)} for p,t in tokens.items()]})
  actor=store.actor(c,gid);human=store.actor(c,hid)
  turn='frozen-reader-turn-'+str(uuid4());thread='frozen-reader-thread-'+str(uuid4())
  c.execute("UPDATE hb_purpose_context SET native_thread=%s,active_turn=%s,accepting=true WHERE agent=%s AND purpose='reader'",(thread,turn,gid))
  # These are explicit frozen model facts, not a real model/admission journey.
  source=store.event(c,human,room,'message','Verified source artifact revision changed.')
  context_room_observations.refresh(c,store,actor)
  obs=c.execute("SELECT * FROM hb_context_material WHERE agent=%s AND source=%s",(gid,source)).fetchone()
  identity=str(obs['id'])
  c.execute("UPDATE hb_purpose_context SET active_material=%s::uuid[] WHERE agent=%s AND purpose='reader'",([identity],gid))
  binding=c.execute("SELECT * FROM hb_purpose_context WHERE agent=%s AND purpose='reader'",(gid,)).fetchone()
  server=TestServer(application(store),host='127.0.0.1');await server.start_server()
  async with ClientSession() as client:
   async def call(action,value,purpose='reader',operation=None):
    body={'runtimeProtocol':'lax-purpose-runtime/v3','operationId':operation or 'role-substrate:'+str(uuid4()),'nativeThread':thread,'nativeTurn':turn,'action':action,'value':value}
    async with client.post(server.make_url('/hollywood/v3/agents/'+gid+'/contexts/'+purpose),json=body,headers={'Authorization':'Bearer '+tokens[purpose]}) as r:return r.status,await r.json()
   async def read(ref):
    v={'reference':ref};full=''
    while True:
     status,r=await call('read',v);assert status==200,(status,r);full+=r['content']
     if r['nextOffset'] is None:return json.loads(full)
     v={'reference':ref,'offset':r['nextOffset'],'revision':r['revision']}
   v={'observationId':identity,'roleRevision':1,'objective':'Update local artifact from verified revision','acceptance':'Actual source and generated result agree','body':'New source affects the installed ongoing responsibility.'}
   await read(identity)
   status,r=await call('initiative',v);mark('unread-role-denied',status==409)
   await read('role')
   with c.transaction(force_rollback=True):
    c.execute("UPDATE hb_agent_role SET revision=revision+1 WHERE principal=%s",(gid,))
    status,r=await call('initiative',v);mark('stale-role-revision-denied',status==409)
   with c.transaction(force_rollback=True):
    c.execute("UPDATE hb_agent_role SET native_goal_scope='work' WHERE principal=%s",(gid,))
    status,r=await call('initiative',v);mark('work-scoped-role-cannot-initiate',status==403)
   with c.transaction(force_rollback=True):
    c.execute("UPDATE hb_event SET audience='direct',recipient=%s WHERE id=%s",(gid,source))
    status,r=await call('initiative',v);mark('private-source-does-not-expand',status==403)
   with c.transaction(force_rollback=True):
    c.execute("UPDATE hb_placement SET enabled=false WHERE agent=%s",(gid,))
    status,r=await call('initiative',v);mark('stopped-placement-denies-initiative',status==403)
   with c.transaction(force_rollback=True):
    c.execute("UPDATE hb_purpose_context SET accepting=false WHERE agent=%s AND purpose='reader'",(gid,))
    status,r=await call('initiative',v);mark('closed-reader-turn-denies-initiative',status==409)
   with c.transaction(force_rollback=True):
    c.execute("INSERT INTO hb_delivery(id,authority,event,agent,state,cancellation_requested) VALUES(%s,%s,%s,%s,'cancelled',true)",(str(uuid4()),aid,source,gid))
    status,r=await call('initiative',v);mark('cancelled-source-denies-initiative',status==409)
   op='role-substrate:accepted:'+str(uuid4());status,r=await call('initiative',v,operation=op)
   mark('native-initiative-accepted',status==200)
   result=r
   status,replay=await call('initiative',v,operation=op)
   mark('original-operation-receipt-deduplicated',status==200 and replay==result)
   status,r=await call('initiative',v)
   mark('different-operation-cannot-reconsume-observation',status==409)
   status,r=await call('initiative',{**v,'body':'changed'},operation=op)
   mark('changed-original-operation-denied',status==409)
   e=store.readable_event(c,actor,result['sourceEventId'])
   mark('not-human-or-self-request',e['kind']=='control' and e['author']==actor['id'] and e['data']['authorKind']=='agent' and e['source']==__import__('uuid').UUID(source))
   mark('private-role-not-automatically-published','role' not in e['data'] and 'roleSnapshot' not in e['data'] and 'interpretation' not in e['data'] and e['data']['roleSha256'])
   mark('one-native-delivery-work-result',c.execute('SELECT count(*) AS n FROM hb_delivery WHERE authority=%s',(aid,)).fetchone()['n']==1 and c.execute("SELECT count(*) AS n FROM hb_context_material WHERE agent=%s AND kind='work'",(gid,)).fetchone()['n']==1 and c.execute("SELECT count(*) AS n FROM hb_context_material WHERE agent=%s AND kind='need'",(gid,)).fetchone()['n']==1)
   mark('no-synthetic-reader-input',not c.execute("SELECT 1 FROM hb_context_material WHERE source=%s AND kind='input'",(e['id'],)).fetchone())
   mark('role-origin-supported-by-existing-goals',context_role_work.origin(c,actor,e))
   c.execute("UPDATE hb_delivery SET host=%s,generation=1 WHERE id=%s",(host,result['deliveryId']))
   async with client.get(server.make_url('/hollywood/v3/connector/deliveries/'+result['deliveryId']+'/context'),
       headers={'Authorization':'Bearer '+tokens['writer']}) as response:
    assert response.status==200
    envelope=await response.json()
   delivery=store.delivery(gid,result['deliveryId'])
   from lax_connector.roles import RoleContext
   captured=RoleContext.capture(envelope,{'agentId':gid,'hostId':host,'workspace':'/fixture/no-execution'},delivery)
   mark('real-context-api-and-lax-capture-admit-only-native-role-origin',captured.value['obligation']=='role_work')
   invalid={**envelope,'executionOrigin':None}
   try:RoleContext.capture(invalid,{'agentId':gid,'hostId':host,'workspace':'/fixture/no-execution'},delivery)
   except ValueError:mark('missing-role-origin-proof-rejected',True)
   else:raise AssertionError('Role flag without native origin proof admitted')

   from hollywood_backbone.attention import human_owner
   try:human_owner(c,e)
   except Exception as err:mark('evidence-human-is-not-role-decision-owner',type(err).__name__=='Conflict')
   else:raise AssertionError('Evidence author selected as role-work decision owner')

   with c.transaction(force_rollback=True):
    c.execute("UPDATE hb_agent_role SET revision=revision+1 WHERE principal=%s",(gid,))
    try:context_role_work.validate(c,store,actor,e)
    except Exception as err:mark('queued-role-work-fenced-on-role-revision',type(err).__name__=='Conflict')
    else:raise AssertionError('Stale role work admitted')
   # The writer credential does not acquire reader-side initiation authority.
   c.execute("UPDATE hb_purpose_context SET native_thread=%s,active_turn=%s,accepting=true WHERE agent=%s AND purpose='writer'",(thread,turn,gid))
   status,r=await call('initiative',v,purpose='writer');mark('writer-cannot-initiate-as-reader',status==403)
  await server.close()
 finally:store.pool=original
 proof={'at':time.time(),'mode':'Real authenticated HTTP/PostgreSQL; frozen native-turn facts; no model/actual execution claim','checks':checks,'modelsStarted':0}
 return proof,aid
try:
 with store.pool.connection() as c:
  with c.transaction(force_rollback=True):
   proof,aid=asyncio.run(check(c))
  assert not c.execute('SELECT 1 FROM hb_authority WHERE id=%s',(aid,)).fetchone()
  proof['checks']['fresh-namespace-fully-rolled-back']=True
  Path('/aosp/development/lax-three-context-20261018-r01/qualification/role-work-substrate-r243.json').write_text(json.dumps(proof,indent=2)+'\n');print(json.dumps(proof))
finally:store.close()
