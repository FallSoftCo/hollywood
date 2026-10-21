"""Real HTTP/PG atomic material and assignment-routing mechanisms; no model proof."""
import sys,os,json,asyncio,secrets,hashlib,time
from pathlib import Path
from uuid import uuid4,uuid5,UUID
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from aiohttp import web,ClientSession
from psycopg.types.json import Jsonb
from hollywood_backbone.store import Store,digest
from hollywood_backbone.server import application
from hollywood_backbone import context_material
async def main():
 os.umask(0o077);root=Path(os.environ.get('ATOMIC_EVIDENCE','/var/lib/lax-atomic-material-20261021-r278/http-final'))
 assert not root.exists();root.mkdir(parents=True,mode=0o700)
 def save(n,v):(root/n).write_text(json.dumps(v,default=str,indent=2)+'\n')
 ids={k:str(uuid4()) for k in ['authority','room','host','workspace','work','large','goal','canonical']}
 creds={n:{'principalId':str(uuid4()),'token':secrets.token_urlsafe(40)} for n in ['Owner','Global']}
 tokens={p:secrets.token_urlsafe(40) for p in ['reader','writer','responder']};agent=creds['Global']['principalId']
 save('intent.json',{'ids':ids,'credentials':creds,'tokens':tokens,'scope':'Synthetic active context metadata and material; actual authenticated HTTP/PG transactions and actual canonical assignment creation. No native/model completion fabricated.'})
 db=json.loads(Path(os.environ.get('ATOMIC_DATABASE','/var/lib/lax-context-rebuild-20261021-r270/database.json')).read_text());s=Store(db);s.migrate()
 s.bootstrap(ids['authority'],'Atomic material mechanism QA',{'globalRoomId':ids['room'],'credentials':creds})
 runner=web.AppRunner(application(s));await runner.setup();port=int(os.environ.get('ATOMIC_PORT','18794'));site=web.TCPSite(runner,'127.0.0.1',port);await site.start()
 url='http://127.0.0.1:'+str(port)+'/hollywood/v3/';checks=[];trace=[]
 try:
  async with ClientSession() as client:
   async def call(method,path,payload=None,who='Owner',status=200):
    token=tokens.get(who) or creds[who]['token']
    async with client.request(method,url+path,json=payload,headers={'Authorization':'Bearer '+token}) as r:
     v=await r.json();trace.append({'method':method,'path':path,'status':r.status,'who':who,'response':v});save('trace.json',trace)
     assert r.status==status,(path,r.status,v);return v
   async def ctx(p,action,value=None,operation=None,status=200,who=None):
    return await call('POST','agents/'+agent+'/contexts/'+p,{'runtimeProtocol':'lax-purpose-runtime/v3','operationId':operation or str(uuid4()),'action':action,'nativeThread':'atomic-'+p,'nativeTurn':'mechanical','value':value or {}},who or p,status)
   await call('POST','placements',{'hostId':ids['host'],'name':'Disclosed nonexecuting mechanism fixture','identityFingerprint':'synthetic-not-a-host','capabilities':{},'agentId':agent})
   await call('PUT','agents/'+agent+'/context-system',{'hostId':ids['host'],'workspaceId':ids['workspace'],'bindings':[{'purpose':p,'contextId':str(uuid4()),'credentialDigest':digest(t)} for p,t in tokens.items()]})
   for p in tokens:assert (await ctx(p,'bind',{'runtimeProtocol':'lax-purpose-runtime/v3','workScheduling':2}))['completeMaterialRead']==1
   source=await call('POST','rooms/'+ids['room']+'/messages',{'operationId':'atomic-original','kind':'request','recipientId':agent,'audience':'room','body':'Disclosed mechanism fixture, not an execution request to a running worker.'})
   event=source['event']['id']
   with s.pool.connection() as c:
    for key,n in [('work',38000),('large',100000)]:
     c.execute("INSERT INTO hb_context_material(id,agent,kind,source,state,value) VALUES(%s,%s,'work',%s,'pending',%s)",(ids[key],agent,event,Jsonb({'objective':'Original immutable objective','acceptance':'Mechanics only','body':'x'*n,'progress':[]})))
    c.execute("UPDATE hb_purpose_context SET active_turn='mechanical',accepting=true,active_material=%s::uuid[] WHERE agent=%s",([event,ids['work'],ids['large']],agent))
   # A real concurrent commit occurs after first chunk fetch. Entire read stays at one cut.
   real=context_material.read;interleaved=False
   def interleave(c,store,actor,purpose,binding,turn,value,**kw):
    nonlocal interleaved
    part=real(c,store,actor,purpose,binding,turn,value,**kw)
    if not interleaved and purpose=='writer' and value.get('reference')==ids['work']:
     interleaved=True
     with s.pool.connection() as other:
      other.execute('UPDATE hb_context_material SET revision=revision+1,value=value || %s WHERE id=%s',(Jsonb({'progress':['Concurrent new fact']}),ids['work']))
    return part
   context_material.read=interleave
   try:old=await ctx('writer','read_complete',{'reference':ids['work']},'atomic-old-cut')
   finally:context_material.read=real
   encoded=''.join(x['content'] for x in old['chunks']);assert old['revision']==1 and len(old['chunks'])>10
   assert hashlib.sha256(encoded.encode()).hexdigest()==old['sha256'] and json.loads(encoded)['value']['progress']==[]
   checks.append('single complete HTTP read is coherent across a concurrent material commit')
   cached=await call('GET','agents/'+agent+'/contexts/writer/operations/atomic-old-cut',who='writer');assert cached==old
   fresh=await ctx('writer','read_complete',{'reference':ids['work']});assert fresh['revision']==2 and fresh['sha256']!=old['sha256']
   checks.append('original receipt immutable; a fresh read sees current revision')
   await ctx('reader','read_complete',{'reference':ids['large']},status=409)
   with s.pool.connection() as c:assert ids['large'] not in c.execute("SELECT reads FROM hb_purpose_context WHERE agent=%s AND purpose='reader'",(agent,)).fetchone()['reads']
   await ctx('responder','read_complete',{'reference':ids['work']},status=403)
   await ctx('writer','read_complete',{'reference':ids['work']},who='reader',status=403)
   chunk=await ctx('writer','read',{'reference':ids['work'],'offset':0});assert chunk['nextOffset']==3072
   checks.append('oversize refuses without proof/truncation; purpose boundaries and legacy paging retained')
   # Real canonical self-assignment, no lifecycle/model fake.
   goal=await call('POST','rooms/'+ids['room']+'/goals',{'operationId':'atomic-goal','id':ids['goal'],'sourceEventId':event,'title':'Fixture Goal','objective':'Mechanics only','acceptance':'No execution claimed','reviewBudget':0})
   work=await call('POST','goals/'+ids['goal']+'/work',{'operationId':'atomic-work','id':ids['canonical'],'revision':goal['goalRevision'],'roomId':ids['room'],'ownerId':agent,'specification':'Own distinct finite assignment','acceptance':'Preserve canonical assignment','dependencies':[]})
   assigned=await call('POST','work/'+ids['canonical']+'/assign',{'operationId':'atomic-assign','revision':work['workRevision']})
   incoming=assigned['requestEventId']
   with s.pool.connection() as c:
    c.execute("UPDATE hb_purpose_context SET active_material=array_append(active_material,%s::uuid) WHERE agent=%s AND purpose='reader'",(incoming,agent))
   read=await ctx('reader','read_complete',{'reference':incoming});body=json.loads(''.join(x['content'] for x in read['chunks']))
   assert body['canonicalAssignment']['workId']==ids['canonical']
   primary=await ctx('reader','inbox',{'materialIds':[incoming]})
   raw=''.join(x['content'] for x in primary['chunks'] if x['reference']==incoming)
   assert json.loads(raw)['canonicalAssignment']['workId']==ids['canonical']
   await ctx('reader','read_complete',{'reference':ids['work']})
   rejected=await ctx('reader','amend',{'inputId':incoming,'workId':ids['work'],'workRevision':2,'body':'Incorrect merge must be rejected'},status=409)
   assert rejected['code']=='CANONICAL_ASSIGNMENT_REQUIRES_WORK'
   answer=await ctx('reader','answer',{'inputId':incoming,'body':'Incorrect answer-only must be rejected'},status=409)
   assert answer['code']==rejected['code']
   accepted=await ctx('reader','accept',{'inputId':incoming,'objective':'Own distinct finite assignment','acceptance':'Preserve assignment'})
   assert accepted['workId']==str(uuid5(UUID(incoming),'work'))
   with s.pool.connection() as c:
    parent=c.execute('SELECT revision,value FROM hb_context_material WHERE id=%s',(ids['work'],)).fetchone()
    child=c.execute('SELECT source,state FROM hb_context_material WHERE id=%s',(accepted['workId'],)).fetchone()
    assert parent['revision']==2 and parent['value']['progress']==['Concurrent new fact']
    assert str(child['source'])==incoming and child['state']=='pending'
   checks.append('canonical self-assignment cannot merge or answer-only; accepted original gets distinct execution material')
   save('result.json',{'at':time.time(),'passed':True,'checks':checks,'modelsStarted':0,'nativeLifecycleProof':False,'sqlMetadataFixturesDisclosed':True,'sourceModules':str(context_material.__file__)})
   print(json.dumps({'passed':True,'checks':len(checks),'modelsStarted':0}))
 finally:await runner.cleanup();s.close()
if __name__=='__main__':asyncio.run(main())
