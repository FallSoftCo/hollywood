"""Bounded retrieval on real PostgreSQL, using rollback-only synthetic material."""
import json,pathlib,uuid,hashlib,datetime
from hollywood_backbone.store import Store,Denied,Conflict
from hollywood_backbone.context_material import index,read,wire,INDEX_BYTES,CHUNK_CHARS
import argparse
parser=argparse.ArgumentParser(description='Run bounded retrieval checks against an already-enrolled disposable context agent; temporary material is rolled back.')
parser.add_argument('--database-config',type=pathlib.Path,required=True)
parser.add_argument('--agent-id',required=True)
parser.add_argument('--source-id',required=True)
parser.add_argument('--evidence-output',type=pathlib.Path)
args=parser.parse_args()
config=json.loads(args.database_config.read_text())
store=Store(config)
proof={'mode':'Actual PostgreSQL transaction, synthetic temporary material, all rolled back; not model-behavior evidence.'}
try:
 with store.pool.connection() as c:
  actor=store.actor(c,args.agent_id)
  source=str(uuid.UUID(args.source_id))
  before=c.execute('SELECT count(*) AS n FROM hb_context_material').fetchone()['n']
  ids=[]
  for n in range(45):
   identity=str(uuid.uuid4());ids.append(identity)
   c.execute("INSERT INTO hb_context_material(id,agent,kind,source,state,value) VALUES(%s,%s,'work',%s,'running',%s::jsonb)",
      (identity,actor['id'],source,json.dumps({'objective':str(n),'body':'𓂀\\quoted"\n'*3000})))
  binding={'active_material':ids,'reads':{}}
  collected=[];cursor=None;pages=0;peak=0
  while True:
   value={'after':cursor} if cursor else {}
   page=index(c,store,actor,'writer',binding,'bounded-test-original-turn',value)
   size=len(wire(page));peak=max(peak,size)
   assert size<=INDEX_BYTES
   collected.extend(x['id'] for x in page['work']);pages+=1
   cursor=page['nextCursor']
   if not cursor:break
   assert pages<20
  assert len(collected)==45 and len(set(collected))==45 and set(collected)==set(ids)
  text=[];offset=0;revision=None;chunks=0;peak_chunk=0
  while True:
   value={'reference':ids[0],'offset':offset}
   if revision:value['revision']=revision
   part=read(c,store,actor,'writer',binding,'bounded-test-original-turn',value)
   binding['reads']=c.execute("SELECT reads FROM hb_purpose_context WHERE agent=%s AND purpose='writer'",
                             (actor['id'],)).fetchone()['reads']
   assert part['offset']==offset
   peak_chunk=max(peak_chunk,len(wire(part)))
   text.append(part['content']);chunks+=1;revision=part['revision']
   if part['nextOffset'] is None:break
   offset=part['nextOffset']
   assert chunks<100
  full=''.join(text)
  assert hashlib.sha256(full.encode()).hexdigest()==part['sha256']
  material=json.loads(full)
  assert material['id']==ids[0] and material['value']['body']=='𓂀\\quoted"\n'*3000
  try:read(c,store,actor,'writer',binding,'bounded-test-original-turn',{'reference':source})
  except Denied:pass
  else:raise AssertionError('Writer original-conversation read was not denied')
  try:read(c,store,actor,'writer',binding,'bounded-test-original-turn',{'reference':ids[0],'revision':999})
  except Conflict:pass
  else:raise AssertionError('Changed revision was not rejected')
  need=str(uuid.uuid4())
  c.execute("INSERT INTO hb_context_material(id,agent,kind,source,state,value) VALUES(%s,%s,'need',%s,'ready',%s::jsonb)",
       (need,actor['id'],source,json.dumps({'material':'material'*1000,'required':True})))
  responder=c.execute("SELECT * FROM hb_purpose_context WHERE agent=%s AND purpose='responder'",(actor['id'],)).fetchone()
  first=read(c,store,actor,'responder',responder,'read-proof',{'reference':need})
  assert first['nextOffset'] is not None
  reads=c.execute("SELECT reads FROM hb_purpose_context WHERE agent=%s AND purpose='responder'",(actor['id'],)).fetchone()['reads']
  assert reads[need]=={'revision':1,'turn':'read-proof','nextOffset':first['nextOffset']}
  while first['nextOffset'] is not None:
   responder={**responder,'reads':reads}
   first=read(c,store,actor,'responder',responder,'read-proof',{'reference':need,'offset':first['nextOffset'],'revision':1})
   reads=c.execute("SELECT reads FROM hb_purpose_context WHERE agent=%s AND purpose='responder'",(actor['id'],)).fetchone()['reads']
  assert reads[need]=={'revision':1,'turn':'read-proof'}
  proof.update({'indexPages':pages,'materialIDsOnce':45,'peakIndexBytes':peak,'detailChunks':chunks,
    'detailContentExact':True,'peakDetailBytes':peak_chunk,'wrongPurposeDenied':True,
    'changedRevisionRejected':True,'publicationReadOnlyCompleteAfterFinalSequentialChunk':True})
  c.rollback()
  assert c.execute('SELECT count(*) AS n FROM hb_context_material').fetchone()['n']==before
  proof['allTemporaryMaterialRolledBack']=True
finally:store.close()
proof['verifiedAt']=datetime.datetime.now(datetime.timezone.utc).isoformat()
if args.evidence_output:
 args.evidence_output.write_text(json.dumps(proof,indent=2)+'\n')
print(json.dumps(proof))
