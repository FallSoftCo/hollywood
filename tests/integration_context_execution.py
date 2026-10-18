"""Real PostgreSQL execution/activity contract; rollback-only, no model proof."""
import argparse, copy, datetime, json
from pathlib import Path
from uuid import uuid4
from psycopg.types.json import Jsonb
from hollywood_backbone.store import Store, Conflict, Denied, digest
from hollywood_backbone.context_system import lifecycle
from hollywood_backbone.context_execution import append_activity
from hollywood_backbone.activity import append_in_transaction

p=argparse.ArgumentParser()
p.add_argument('--database-config',type=Path,required=True)
p.add_argument('--agent-id',required=True)
p.add_argument('--work-id',required=True)
p.add_argument('--evidence-output',type=Path,required=True)
args=p.parse_args()
store=Store(json.loads(args.database_config.read_text()))
proof={'schema':'lax-purpose-execution-pg-proof/v1','mode':'Actual PostgreSQL, rollback-only synthetic turn/material lifecycle; no model execution or production effects.'}
try:
 with store.pool.connection() as c:
  a=store.actor(c,args.agent_id)
  before=c.execute('SELECT count(*) AS n FROM hb_event').fetchone()['n']
  original=c.execute('SELECT * FROM hb_context_material WHERE id=%s AND agent=%s',(args.work_id,a['id'])).fetchone()
  assert original
  c.execute("UPDATE hb_context_material SET state='pending' WHERE id=%s",(args.work_id,))
  c.execute("UPDATE hb_delivery SET state='running',cancellation_requested=false WHERE event=%s AND agent=%s",(original['source'],a['id']))
  turns=['rollback-scope-'+str(uuid4()),'rollback-scope-'+str(uuid4())]
  scopes=[]
  for turn in turns:
   binding=c.execute("SELECT * FROM hb_purpose_context WHERE agent=%s AND purpose='writer'",(a['id'],)).fetchone()
   params={'operationId':'context-begin:'+turn,'action':'begin',
           'nativeThread':binding['native_thread'],'nativeTurn':turn,'value':{'materialIds':[args.work_id]}}
   admission=lifecycle(c,store,a,'writer',binding,params)
   assert len(admission['executionScopes'])==1
   scope=admission['executionScopes'][0];scopes.append(scope)
   c.execute('INSERT INTO hb_context_operation VALUES(%s,%s,%s,%s,%s)',
             (a['id'],'writer',params['operationId'],digest(params),Jsonb(admission)))
   active=c.execute("SELECT * FROM hb_purpose_context WHERE agent=%s AND purpose='writer'",(a['id'],)).fetchone()
   lifecycle(c,store,a,'writer',active,{**params,'action':'end','value':{'status':'completed'}})
  assert scopes[0]['id']!=scopes[1]['id']
  assert all(s['generation']==scopes[0]['generation'] and s['claimId']==scopes[0]['claimId']
             and s['materialId']==args.work_id and s['deliveryId']==scopes[0]['deliveryId'] for s in scopes)
  c.execute("UPDATE hb_delivery SET state='completed' WHERE id=%s",(scopes[0]['deliveryId'],))
  event_count=c.execute('SELECT count(*) AS n FROM hb_event').fetchone()['n']
  def frame(scope,item='command'):
   return {'operationId':'rollback-log-'+str(uuid4()),'action':'activity',
     'nativeThread':scope['nativeThread'],'nativeTurn':scope['nativeTurn'],
     'value':{'scopeId':scope['id'],'payload':{
      'claimId':scope['claimId'],'generation':scope['generation'],'nativeThread':scope['nativeThread'],
      'nativeTurn':scope['nativeTurn'],'itemId':item,'phase':'completed',
      'nativeItemDigest':'a'*64,'item':{'nativeType':'commandExecution','title':'Command',
       'status':'completed','command':'printf scope-proof','output':'scope-proof','exitCode':0}}}}
  writer=c.execute("SELECT * FROM hb_purpose_context WHERE agent=%s AND purpose='writer'",(a['id'],)).fetchone()
  frames=[frame(s) for s in scopes]
  accepted=[append_activity(c,store,a,'writer',writer,f) for f in frames]
  assert all(r['deliveries']==[] and r['event']['data']['type']=='executionActivity' for r in accepted)
  assert all(r['event']['data']['executionScope']['id']==s['id'] for r,s in zip(accepted,scopes))
  repeated=append_activity(c,store,a,'writer',writer,frames[0])
  assert repeated['event']['id']==accepted[0]['event']['id']
  assert c.execute('SELECT count(*) AS n FROM hb_event').fetchone()['n']==event_count+2
  changed=copy.deepcopy(frames[0]);changed['value']['payload']['item']['output']='changed'
  try:append_activity(c,store,a,'writer',writer,changed)
  except Conflict:pass
  else:raise AssertionError('Changed immutable log accepted')
  wrong=copy.deepcopy(frames[0]);wrong['value']['payload']['nativeTurn']=scopes[1]['nativeTurn']
  try:append_activity(c,store,a,'writer',writer,wrong)
  except Conflict:pass
  else:raise AssertionError('Cross-turn activity accepted')
  reader=c.execute("SELECT * FROM hb_purpose_context WHERE agent=%s AND purpose='reader'",(a['id'],)).fetchone()
  try:append_activity(c,store,a,'reader',reader,frames[0])
  except Denied:pass
  else:raise AssertionError('Reader published public execution activity')
  from hollywood_backbone.activity_models import normalize
  try:append_in_transaction(store,c,a,scopes[0]['deliveryId'],normalize(frames[0]['value']['payload']))
  except Denied:pass
  else:raise AssertionError('Purpose agent bypassed scoped route')
  assert c.execute('SELECT state FROM hb_delivery WHERE id=%s',(scopes[0]['deliveryId'],)).fetchone()['state']=='completed'
  proof.update({'twoTurnsSameOriginalWorkDeliveryAndGeneration':True,'differentImmutableScopes':True,
    'closedOriginalTurnsAcceptedLateLogs':True,'metadataGrantedBusinessAuthority':False,
    'duplicateLogSameReceipt':True,'changedLogDenied':True,'crossTurnDenied':True,
    'readerPublicActivityDenied':True,'unscopedPurposeLegacyRouteDenied':True,
    'createdDeliveries':0,'changedTerminalWorkState':False,'originalExecutionReplayed':False})
  c.rollback()
  assert c.execute('SELECT count(*) AS n FROM hb_event').fetchone()['n']==before
  proof['syntheticRecordsRolledBack']=True
finally:store.close()
proof['at']=datetime.datetime.now(datetime.timezone.utc).isoformat()
args.evidence_output.write_text(json.dumps(proof,indent=2)+'\n')
print(json.dumps(proof))
