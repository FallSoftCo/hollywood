"""One disclosed copied-realm human fixture; discard accepted reply then recover.
No live endpoint changes, phone operations, models or retry submissions.
"""
import asyncio
import hashlib
import json
import time
from pathlib import Path
from aiohttp import web
from aiohttp.test_utils import TestServer
from hollywood_backbone.store import Store
from hollywood_backbone.server import application

ROOT=Path('/var/lib/lax-live-promotion-preview-20261021-r260')
Q=Path('/aosp/development/lax-three-context-20261018-r01/qualification')
OP='copy-native-human-receipt-r264'
A='e488f19b-0f60-46ca-bd48-4fe544f06757'
G='2d4a0daa-0514-475e-be1e-71decacbd5bc'
H='301c8b83-8cdb-5b9a-9d7f-3b2fa6f0b3f6'
R='3dd596b7-7a73-4828-969b-335c130551dc'
BINDING=Path('/home/aosp/.local/share/nextcomp-backend/data/hollywood.json')
COMMON=r"""
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {loadHollywood,HollywoodClient,hollywoodRequest} from '/home/aosp/.local/share/nextcomp-backend/releases/20261017-native-core-alpha110-r03/server/hollywood.mjs';
const cfg=loadHollywood('/home/aosp/.local/share/nextcomp-backend/data');
assert(cfg&&!cfg.invalid);
const binding=cfg.devices.get('owner')||cfg.defaultBinding||cfg.devices.values().next().value;
assert.equal(binding.authorityId,'e488f19b-0f60-46ca-bd48-4fe544f06757');
assert.equal(binding.principalId,'301c8b83-8cdb-5b9a-9d7f-3b2fa6f0b3f6');
const candidate={...binding,url:process.argv[1]};
const op='copy-native-human-receipt-r264';
"""
SEND=r"""
let posts=0;
const client=new HollywoodClient(candidate,{fetchImpl:async(url,options)=>{
 const reply=await fetch(url,options);
 if(options.method==='POST'){
  posts++;assert.equal(reply.status,200);
  await reply.text(); // Server acceptance confirmed to the fault injector only.
  throw Error('Qualification: accepted reply deliberately lost');
 }
 return reply;
}});
const body={operationId:op,kind:'request',audience:'room',recipientId:'2d4a0daa-0514-475e-be1e-71decacbd5bc',body:'COPY ONLY disclosed migration receipt fixture; no model or business execution. Verify this original receipt rather than submitting again.'};
const request=hollywoodRequest('POST','rooms/'+binding.defaultRoomId+'/messages',{},body);
let error;
try{await client.call(request.path,request);}catch(value){error=value;}
assert.equal(error?.code,'HOLLYWOOD_OUTCOME_UNCONFIRMED');
assert.equal(posts,1);
console.log(JSON.stringify({posts,clientOutcome:error.code,scriptedHumanFixture:true}));
"""
RECOVER=r"""
const client=new HollywoodClient(candidate);
const request=hollywoodRequest('GET','commands/'+op,{},undefined);
const receipt=await client.call(request.path,request);
assert.equal(receipt.event.author,binding.principalId);
assert.equal(receipt.event.authority,binding.authorityId);
assert.equal(receipt.event.recipient,'2d4a0daa-0514-475e-be1e-71decacbd5bc');
assert.equal(receipt.event.kind,'request');
assert.equal(receipt.deliveries.length,1);
assert.equal(receipt.deliveries[0].state,'pending');
const room=(await client.call('rooms')).rooms.find(x=>x.id===binding.defaultRoomId);
assert.equal(room.activity.version,1);assert.equal(room.activity.pending,1);
console.log(JSON.stringify({eventId:receipt.event.id,deliveryId:receipt.deliveries[0].id,
state:receipt.deliveries[0].state,authorId:receipt.event.author,recipientId:receipt.event.recipient,
catalogActivity:room.activity,posts:0}));
"""

async def node(code,url,phase):
 process=await asyncio.create_subprocess_exec('runuser','-u','aosp','--','node','--input-type=module','-e',COMMON+code,str(url),
      stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
 stdout,stderr=await asyncio.wait_for(process.communicate(),timeout=60)
 (ROOT/('gateway-write-'+phase+'-stdout.log')).write_bytes(stdout)
 (ROOT/('gateway-write-'+phase+'-stderr.log')).write_bytes(stderr)
 assert process.returncode==0,'Inspect protected '+phase+' logs and original operation; do not repeat POST'
 return json.loads(stdout)

async def main():
 output=Q/'live-gateway-write-recovery-r264.json'
 assert not output.exists()
 config=json.loads((ROOT/'database.json').read_text())
 probe=Store(config)
 with probe.pool.connection() as c:
  assert c.execute('SELECT current_database() AS n').fetchone()['n']=='harness_native_preview_20261021_r260'
  assert not c.execute('SELECT 1 FROM hb_command WHERE principal=%s AND operation=%s',(H,OP)).fetchone(),'Original operation exists; inspect, do not send'
 probe.close()
 binding_before=hashlib.sha256(BINDING.read_bytes()).hexdigest()
 requests=[];posts=0;servers=[]
 @web.middleware
 async def bounded(request,handler):
  nonlocal posts
  if request.method=='POST':
   payload=await request.json()
   if request.path!='/hollywood/v3/rooms/'+R+'/messages' or payload.get('operationId')!=OP or posts:
    return web.json_response({'error':'Outside one copied fixture'},status=403)
   posts+=1
  elif request.method!='GET':
   return web.json_response({'error':'No other mutations'},status=403)
  requests.append({'method':request.method,'path':request.path})
  return await handler(request)
 async def serve():
  store=Store(config);app=application(store);app.middlewares.insert(0,bounded)
  server=TestServer(app,host='127.0.0.1');await server.start_server()
  servers.append((server,store));return server
 report={'schema':'lax-live-gateway-copy-write-recovery/v1','modelsStarted':0,'servicesChanged':False,
         'scriptedHumanFixture':True,'operationId':OP,'scope':__doc__}
 try:
  first=await serve()
  report['send']=await node(SEND,first.make_url('/'),'send')
  await first.close();servers[0][1].close()
  second=await serve()
  report['recovery']=await node(RECOVER,second.make_url('/'),'recover')
  with servers[1][1].pool.connection() as c:
   original=c.execute('SELECT * FROM hb_command WHERE principal=%s AND operation=%s',(H,OP)).fetchall()
   assert len(original)==1
   event=original[0]['event']
   assert str(event)==report['recovery']['eventId']
   deliveries=c.execute('SELECT * FROM hb_delivery WHERE event=%s',(event,)).fetchall()
   assert len(deliveries)==1 and deliveries[0]['generation']==0 and deliveries[0]['claim_id'] is None
   inputs=c.execute("SELECT * FROM hb_context_material WHERE agent=%s AND kind='input' AND source=%s",(G,event)).fetchall()
   assert len(inputs)==1 and inputs[0]['state']=='pending'
   report.update(originalCommands=1,originalDeliveries=1,readerInputs=1,originalGeneration=0,
                 interpretationOrExecutionClaimed=False,totalPosts=posts,passed=True)
  assert hashlib.sha256(BINDING.read_bytes()).hexdigest()==binding_before
  report['liveBindingUnchanged']=True
 finally:
  for server,store in servers:
   await server.close();store.close()
  report.update(at=time.time(),listenersRetired=True,requests=requests,liveChanges=False)
  output.write_text(json.dumps(report,indent=2)+'\n')
 print(json.dumps(report))
if __name__=='__main__':asyncio.run(main())
