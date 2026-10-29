import pathlib,json,uuid,datetime
from psycopg.types.json import Jsonb
from hollywood_backbone.store import Store,Conflict
from hollywood_backbone.context_system import item
from hollywood_backbone import context_peers as peers
from hollywood_backbone.communication import completion
import argparse
parser=argparse.ArgumentParser(description='Rollback-only peer protocol check with an already-enrolled disposable pair.')
parser.add_argument('--database-config',type=pathlib.Path,required=True)
parser.add_argument('--parent-agent-id',required=True)
parser.add_argument('--peer-agent-id',required=True)
parser.add_argument('--source-id',required=True)
parser.add_argument('--evidence-output',type=pathlib.Path)
args=parser.parse_args()
store=Store(json.loads(args.database_config.read_text()))
proof={'mode':'Real PostgreSQL, rollback-only synthetic material. Not model behavior proof.'}
try:
 with store.pool.connection() as c:
  a=store.actor(c,args.parent_agent_id)
  peer=store.actor(c,args.peer_agent_id)
  before=c.execute('SELECT count(*) AS n FROM hb_event').fetchone()['n']
  workid=str(uuid.uuid4())
  source=str(uuid.UUID(args.source_id))
  c.execute("""INSERT INTO hb_context_material(id,agent,kind,source,state,value)
    VALUES(%s,%s,'work',%s,'running',%s)""",(workid,a['id'],source,Jsonb({'objective':'rollback-only review','acceptance':'reviewed','progress':[],'evidence':[]})))
  binding=c.execute("SELECT * FROM hb_purpose_context WHERE agent=%s AND purpose='writer'",(a['id'],)).fetchone()
  binding['reads']={workid:{'revision':1,'turn':'rollback-writer-turn'}}
  p={'nativeTurn':'rollback-writer-turn','operationId':'rollback-peer-intent','value':{
    'workId':workid,'recipientId':str(peer['id']),'body':'Review the supplied frozen state; retain IDs and declared states.'}}
  queued=peers.queue(c,store,a,binding,p)
  assert queued['state']=='queued'
  assert c.execute('SELECT count(*) AS n FROM hb_event').fetchone()['n']==before
  binding=c.execute("SELECT * FROM hb_purpose_context WHERE agent=%s AND purpose='writer'",(a['id'],)).fetchone()
  waiting=peers.wait(c,store,a,binding,{'nativeTurn':p['nativeTurn'],'value':{'workId':workid}})
  assert waiting['state']=='waiting'
  need=item(c,store,a,queued['communicationId'],'need')
  accepted=peers.publish(c,store,a,need,{'schema':'rollback-only-substrate-test'},None)
  assert accepted['event']['author']==str(a['id']) and accepted['event']['data']['authorKind']=='agent'
  assert accepted['event']['kind']=='request' and accepted['event']['data']['expectsReply']
  child=accepted['deliveries'][0]
  assert child['agent']==str(peer['id'])
  work=item(c,store,a,workid,'work')
  binding=c.execute("SELECT * FROM hb_purpose_context WHERE agent=%s AND purpose='writer'",(a['id'],)).fetchone()
  try:peers.require_current_read(binding,work,p['nativeTurn'])
  except Conflict:pass
  else:raise AssertionError('New accepted receipt did not invalidate stale writer material')
  request=store.readable_event(c,peer,accepted['event']['id'])
  result=store.event(c,peer,request['room'],'message','Independent review complete; frozen state only.',
     audience=request['audience'],source=request['id'])
  delivery=c.execute("UPDATE hb_delivery SET state='completed',result_event=%s WHERE id=%s RETURNING *",(result,child['id'])).fetchone()
  continuation=completion(store,c,peer,request,delivery,result,'completed')
  assert continuation
  continuation_row=c.execute('SELECT * FROM hb_event WHERE id=%s',(continuation,)).fetchone()
  assert continuation_row['kind']=='result' and not c.execute('SELECT id FROM hb_delivery WHERE event=%s',(continuation,)).fetchone()
  incoming=item(c,store,a,str(continuation_row['id']),'input')
  assert incoming['value']['responseRequired'] is False and incoming['value']['parentWorkId']==workid
  assert not c.execute("SELECT 1 FROM hb_context_material WHERE source=%s AND kind='need'",(incoming['source'],)).fetchone()
  count=c.execute('SELECT count(*) AS n FROM hb_event').fetchone()['n']
  applied=peers.apply(c,store,a,{}, {'nativeTurn':'rollback-reader-turn','value':{
    'inputId':str(incoming['id']),'body':'Peer result was reviewed; original work can continue from this evidence.'}})
  assert applied['state']=='original_work_ready' and applied['responseRequired'] is False
  work=item(c,store,a,workid,'work')
  assert work['state']=='pending' and work['value']['resumeRevision']==1
  assert work['value']['peerRequests'][0]['peerResult']['resultEventId']==str(result)
  assert c.execute('SELECT count(*) AS n FROM hb_event').fetchone()['n']==count
  try:peers.apply(c,store,a,{}, {'value':{'inputId':str(incoming['id']),'body':'duplicate'},'nativeTurn':'rollback-reader-turn'})
  except Conflict:pass
  else:raise AssertionError('Repeated completion input was applied twice')
  proof.update({'queuedWasNotAccepted':True,'normalCanonicalRequestAccepted':True,'distinctPeerRecipient':True,
    'staleWriterContributionDenied':True,'canonicalPeerCompletionMappedOriginalWork':True,
    'noAcknowledgementObligation':True,'readerResumedOriginalWorkOnce':True,'readerResultAddedRoomMessages':0})
  c.rollback()
  assert c.execute('SELECT count(*) AS n FROM hb_event').fetchone()['n']==before
  proof['allSyntheticStateRolledBack']=True
finally:store.close()
proof['at']=datetime.datetime.now(datetime.timezone.utc).isoformat()
if args.evidence_output:args.evidence_output.write_text(json.dumps(proof,indent=2)+'\n')
print(json.dumps(proof))
