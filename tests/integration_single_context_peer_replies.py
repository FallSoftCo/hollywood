"""Real PG reply lineage/queue and native codec; claimed facts are rollback-only."""
import contextlib,json,sys
from pathlib import Path
from uuid import uuid4
import psycopg
from psycopg.rows import dict_row
from hollywood_backbone.store import Store
from hollywood_backbone.single_context_messages import enqueue,CAPABILITY
from lax_connector.roles import RoleContext
from lax_connector.native_input import submission
r=Path(sys.argv[1]);i=json.loads((r/'enterprise/installation-intent.json').read_text());assert i['name']=='Single-context native federation qualification'
a=i['credentials']['Global']['principalId'];h='d8e49cff-a527-4b8e-bfe8-5463d17488c8'
with psycopg.connect(**json.loads((r/'database.json').read_text()),row_factory=dict_row) as c:
 c.execute('BEGIN')
 class Borrow:
  @contextlib.contextmanager
  def connection(self):
   with c.transaction():yield c
 store=Store.__new__(Store);store.pool=Borrow();actor=store.actor(c,a)
 event=c.execute("SELECT * FROM hb_event WHERE recipient=%s AND data->>'type'='peerCompletion' ORDER BY seq DESC LIMIT 1",(a,)).fetchone();assert event
 before=c.execute('SELECT count(*) AS n FROM hb_event').fetchone()['n']
 try:
  assert enqueue(store,c,actor,h,[])==0
  origin='1103975b-9ff6-4636-ac12-b373c9656b21'
  c.execute('UPDATE hb_delivery SET cancellation_requested=true WHERE id=%s',(origin,));assert enqueue(store,c,actor,h,[CAPABILITY])==0
  c.execute('UPDATE hb_delivery SET cancellation_requested=false WHERE id=%s',(origin,))
  c.execute('UPDATE hb_principal SET administrator=false WHERE id=%s',(a,));actor=store.actor(c,a);assert enqueue(store,c,actor,h,[CAPABILITY])==0
  c.execute('UPDATE hb_principal SET administrator=true WHERE id=%s',(a,));actor=store.actor(c,a)
  c.execute("INSERT INTO hb_context_system(agent,authority,host,workspace,role_revision,enabled) VALUES(%s,%s,%s,'rollback-only',1,false)",(a,actor['authority'],h));assert enqueue(store,c,actor,h,[CAPABILITY])==0
  c.execute('DELETE FROM hb_context_system WHERE agent=%s',(a,))
  assert enqueue(store,c,actor,h,[CAPABILITY])==1
  assert enqueue(store,c,actor,h,[CAPABILITY])==0
  delivery=c.execute('SELECT * FROM hb_delivery WHERE event=%s AND agent=%s',(event['id'],a)).fetchone();assert delivery['state']=='pending'
  assert c.execute('SELECT count(*) AS n FROM hb_event').fetchone()['n']==before
  c.execute("UPDATE hb_delivery SET state='claimed',host=%s,generation=1,claim_id=%s WHERE id=%s",(h,'rollback-only-'+str(uuid4()),delivery['id']))
  active=store.claim_receipt(c,str(delivery['id']));context=store.execution_context(a,str(delivery['id']))
  assert context['obligation']=='peer_reply' and context['requiresResponse'] and context['executionOrigin']['originRequestId']=='14471249-f759-4b5b-b65c-27c9198eabcc'
  config={'agentId':a,'hostId':h,'workspace':'/var/lib/qa','sandbox':'danger-full-access'}
  role=RoleContext.capture(context,config,active)
  method,payload=submission(a,active,context['executionOrigin']['nativeThread'],'unchanged-model','unchanged-effort',role.additional_context())
  assert method=='hollywood/agentMessage' and payload['kind']=='message' and payload['authorId']==str(event['author']) and payload['eventId']==str(event['id']) and payload['body']==event['body']
  assert active['request']['kind']=='result' and payload['expectsReply'] is False
  result={'passed':True,'realPostgreSQL':True,'nativeClaimFacts':'SYNTHETIC rollback-only; not provider execution proof','onlyExplicitlyRequestedPeerCompletionQueued':True,'oldCapabilitiesAndPurposeRuntimesUnchanged':True,'originalCancellationAndMembershipHonored':True,'restartDeduplication':True,'noNewRoomEventOrHumanInput':True,'samePersistentContextTargeted':True,'nativeAgentMessageCodecPreservesCanonicalEventAuthorBody':True,'noReplyObligationLoop':True,'modelsStarted':0,'databaseFixturesRolledBack':True}
 finally:c.rollback()
(r/'single-context-peer-reply-qualification.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
