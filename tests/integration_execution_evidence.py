"""Real HTTP/PostgreSQL; execution/checkpoint rows are explicit metadata fixtures.

No Linux/model worker or original business task is run. Native semantic proof is
a separate release requirement.
"""
import asyncio,json,os,secrets,sys,time
from pathlib import Path
from uuid import uuid4
from psycopg.types.json import Jsonb
from aiohttp import ClientSession,web
sys.path.insert(0,str(Path(__file__).parents[1]))
from hollywood_backbone.server import application
from hollywood_backbone.store import Store,digest,json_value

async def main():
 os.umask(0o077);root=Path(os.environ['EXECUTION_EVIDENCE_OUTPUT']);assert not root.exists();root.mkdir(mode=0o700)
 uid=lambda:str(uuid4())
 ids={k:uid() for k in ['authority','source','project']}
 cred={k:{'principalId':uid(),'token':secrets.token_urlsafe(40)} for k in ['Owner','Global','Worker','Peer']}
 (root/'intent.json').write_text(json.dumps({'ids':ids,'credentials':cred,'explicitMetadataFixtures':True,'models':0},indent=2))
 store=Store(json.loads(Path(os.environ['EXECUTION_EVIDENCE_DATABASE']).read_text()))
 store.bootstrap(ids['authority'],'Execution evidence HTTP fixture',{'globalRoomId':ids['source'],'credentials':{k:cred[k] for k in ['Owner','Global']}})
 runner=web.AppRunner(application(store));await runner.setup();site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
 base='http://127.0.0.1:'+str(site._server.sockets[0].getsockname()[1])+'/hollywood/v3/'
 checks={};trace=[]
 def sql(query,args=()):
  with store.pool.connection() as c:
   r=c.execute(query,args)
   return json_value(r.fetchall()) if r.description else None
 try:
  async with ClientSession() as http:
   async def call(who,method,path,payload=None,status=200):
    async with http.request(method,base+path,json=payload,headers={'Authorization':'Bearer '+cred[who]['token']}) as r:
     value=await r.json();trace.append({'actor':who,'method':method,'path':path,'status':r.status,'response':value})
     (root/'http-trace.json').write_text(json.dumps(trace,indent=2))
     assert r.status==status,(method,path,r.status,value)
     return value
   for who in ['Worker','Peer']:
    await call('Owner','POST','principals',{'id':cred[who]['principalId'],'name':'Fixture '+who,'kind':'agent','enrollmentCredential':cred[who]['token']})
   await call('Owner','POST','rooms',{'id':ids['project'],'name':'Evidence reviewers','purpose':'Selected original proof without source room access','ownerId':cred['Global']['principalId']})
   async def grant(who,room,revision,read=True,post=True):
    return await call('Owner','PUT','rooms/'+room+'/membership',{'principalId':cred[who]['principalId'],'revision':revision,'can_read':read,'can_post':post,'can_request':post})
   await grant('Worker',ids['source'],0);await grant('Worker',ids['project'],0);await grant('Peer',ids['project'],0,post=False)
   await call('Owner','PUT','agents/'+cred['Worker']['principalId']+'/communication',{'operationId':'fixture-enroll-worker','credentialDigest':digest(cred['Worker']['token']),'allowRequests':True})
   original=await call('Owner','POST','rooms/'+ids['source']+'/messages',{'operationId':'fixture-original','kind':'request','audience':'room','recipientId':cred['Worker']['principalId'],'body':'Explicit nonexecuting metadata fixture'})
   d=original['deliveries'][0]['id'];source=original['event']['id'];agent=cred['Worker']['principalId']
   def fixture_event(body,data,kind='progress',audience='room'):
    with store.pool.connection() as c:
     a=store.actor(c,agent)
     return str(store.event(c,a,ids['source'],kind,body,source=source,data=data,audience=audience,recipient=agent if audience=='direct' else None))
   activity=fixture_event('Declared fixture command',{'type':'executionActivity','deliveryId':d,'phase':'completed','itemId':'fixture-action','output':'SYNTHETIC original output; no executable command','exitCode':0})
   oversized=fixture_event('Oversized declared fixture',{'type':'executionActivity','deliveryId':d,'output':'x'*20000})
   private=fixture_event('Private declared fixture',{'type':'executionActivity','deliveryId':d},audience='direct')
   operation='fixture-installed-original';snapshotOperation='fixture-snapshot-original';receipt={'schema':'lax-native-idle-context-installed/v1','status':'recorded','generation':1,'snapshotOperationId':snapshotOperation,'nativeReceipt':{'snapshotSha256':'a'*64},'verification':'Explicit synthetic metadata, not native proof'}
   snapshot={'schema':'lax-context-rebuild-snapshot/v1','sha256':'a'*64,'chunks':[{'content':json.dumps({'obligations':[{'source':source,'delivery_id':d}],'privateFixtureInput':'MUST_NOT_PUBLISH'})}]}
   sql('INSERT INTO hb_context_operation(agent,purpose,operation,digest,result) VALUES(%s,%s,%s,%s,%s)',(agent,'writer',operation,'b'*64,Jsonb(receipt)))
   sql('INSERT INTO hb_context_operation(agent,purpose,operation,digest,result) VALUES(%s,%s,%s,%s,%s)',(agent,'writer',snapshotOperation,'c'*64,Jsonb(snapshot)))
   index=await call('Worker','GET','deliveries/'+d+'/evidence')
   checks['original_index_without_model_or_execution']=any(x['eventId']==activity for x in index['events']) and index['contextOperations']==[{'operationId':operation,'schema':receipt['schema'],'generation':1}]
   await call('Peer','GET','deliveries/'+d+'/evidence',status=403)
   refs={'version':1,'deliveryId':d,'eventIds':[activity],'contextOperationIds':[operation]}
   body={'operationId':'fixture-one-publication','kind':'message','audience':'room','body':'Selected original authority evidence','evidenceReferences':refs}
   accepted=await call('Worker','POST','rooms/'+ids['project']+'/messages',body)
   publication=accepted['event'];proof=publication['data']['executionEvidence']
   canonical=sql('SELECT * FROM hb_event WHERE id=%s',(activity,))[0]
   checks['authority_copies_exact_original_records']=proof['records']==[{'kind':'event','event':canonical},{'kind':'contextOperation','agentId':agent,'purpose':'writer','operationId':operation,'result':receipt}]
   unsealed={k:v for k,v in proof.items() if k!='sha256'}
   checks['digest_and_no_snapshot_private_inputs']=proof['sha256']==digest(unsealed) and 'MUST_NOT_PUBLISH' not in json.dumps(proof)
   history=await call('Peer','GET','rooms/'+ids['project']+'/history?view=conversation&limit=100')
   checks['peer_reads_published_original_proof']=next(e for e in history['events'] if e['id']==publication['id'])['data']['executionEvidence']==proof
   await call('Peer','GET','inbox/events/'+activity,status=403)
   await call('Peer','GET','rooms/'+ids['source']+'/history?limit=100',status=403)
   checks['source_room_and_original_event_remain_forbidden']=True
   readback=await call('Worker','GET','commands/'+body['operationId'])
   checks['original_command_readback_no_repeat_post']=all(readback[k]==v for k,v in accepted.items()) and readback['canonicalCommand']['eventId']==accepted['event']['id'] and len(sql("SELECT id FROM hb_event WHERE authority=%s AND data->'executionEvidence'->>'deliveryId'=%s",(ids['authority'],d)))==1
   async def denied(name,reference,status=403,who='Worker'):
    before=len(sql('SELECT id FROM hb_event WHERE authority=%s',(ids['authority'],)))
    await call(who,'POST','rooms/'+ids['project']+'/messages',{'operationId':name,'kind':'message','audience':'room','body':'Explicit negative fixture','evidenceReferences':reference},status=status)
    assert before==len(sql('SELECT id FROM hb_event WHERE authority=%s',(ids['authority'],)))
   await denied('no-human-input-forward',{**refs,'eventIds':[source],'contextOperationIds':[]})
   await denied('no-private-event',{**refs,'eventIds':[private],'contextOperationIds':[]})
   await denied('no-foreign-owned-delivery',{**refs,'deliveryId':uid()})
   await denied('no-other-actor-publication',refs,who='Peer')
   await denied('no-oversized-evidence',{**refs,'eventIds':[oversized],'contextOperationIds':[]},400)
   await denied('no-snapshot-content-as-receipt',{**refs,'eventIds':[],'contextOperationIds':[snapshotOperation]})
   await denied('no-duplicate-reference',{**refs,'eventIds':[activity,activity]},400)
   await denied('no-boolean-version',{**refs,'version':True},400)
   await denied('no-empty-selection',{**refs,'eventIds':[],'contextOperationIds':[]},400)
   checks['invalid_private_control_owned_scope_and_bounds_no_partial_event']=True
   await grant('Worker',ids['source'],1,read=False,post=False)
   await denied('revoked-original-source',refs)
   after=await call('Peer','GET','rooms/'+ids['project']+'/history?view=conversation&limit=100')
   checks['committed_publication_retained_after_source_access_revoked']=next(e for e in after['events'] if e['id']==publication['id'])['data']['executionEvidence']==proof
   await grant('Worker',ids['source'],2)
   await grant('Worker',ids['project'],1,post=False)
   await denied('revoked-publication-target',refs)
   checks['current_source_and_target_permissions_rechecked']=True
   checks['no_new_work_from_evidence_message']=accepted['deliveries']==[] and len(sql('SELECT id FROM hb_delivery WHERE authority=%s',(ids['authority'],)))==1
   # Actual public HTTP input contract preserves exact old default field shape.
   await grant('Worker',ids['project'],2)
   plain=await call('Worker','POST','rooms/'+ids['project']+'/messages',{'operationId':'fixture-private-default-compatibility','kind':'message','body':'No selected evidence'})
   checks['omitted_feature_preserves_plain_communication']='executionEvidence' not in plain['event']['data']
   result={'at':time.time(),'passed':all(checks.values()),'checks':checks,'httpCalls':len(trace),'authority':ids['authority'],'modelCalls':0,'linuxWorkers':0,'metadataFixtures':True,'nativeSemanticQualification':False,'productionChanged':False}
   (root/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result));assert result['passed']
 finally:
  await runner.cleanup();store.close()
if __name__=='__main__':asyncio.run(main())
