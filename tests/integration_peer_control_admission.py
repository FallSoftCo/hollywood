"""Real PG control-slot admission; synthetic negative fixtures rolled back."""
import contextlib,json,sys
from pathlib import Path
from uuid import uuid4
import psycopg
from psycopg.rows import dict_row
from hollywood_backbone.store import Store
from hollywood_backbone.admission import diagnose
from lax_connector.native_tool_catalog import CAPABILITIES
r=Path(sys.argv[1]);i=json.loads((r/'enterprise/installation-intent.json').read_text());assert i['name']=='Single-context native federation qualification'
a=i['credentials']['Global']['principalId'];h='d8e49cff-a527-4b8e-bfe8-5463d17488c8'
with psycopg.connect(**json.loads((r/'database.json').read_text()),row_factory=dict_row) as c:
 c.execute('BEGIN')
 class Borrow:
  @contextlib.contextmanager
  def connection(self):
   with c.transaction():yield c
 store=Store.__new__(Store);store.pool=Borrow();actor=store.actor(c,a)
 event=c.execute('SELECT * FROM hb_event WHERE seq=42').fetchone();reply=c.execute('SELECT * FROM hb_delivery WHERE event=%s AND agent=%s',(event['id'],a)).fetchone();assert reply and reply['state']=='pending'
 policy=c.execute('SELECT * FROM hb_host_admission WHERE host=%s',(h,)).fetchone();assert policy['max_concurrent']==1 and policy['reserved_interactive']==1
 try:
  eligible=diagnose(store,c,actor,h,CAPABILITIES,record=False)
  assert str(eligible['eligibleDeliveryId'])==str(reply['id']) and eligible['queueClass']=='interactive',eligible
  assert eligible['capacity']['workerLimit']==0 and event['data']['authorKind']=='agent'
  parent='1103975b-9ff6-4636-ac12-b373c9656b21'
  c.execute('UPDATE hb_delivery SET cancellation_requested=true WHERE id=%s',(parent,));blocked=diagnose(store,c,actor,h,CAPABILITIES);assert blocked['eligibleDeliveryId'] is None
  c.execute('UPDATE hb_delivery SET cancellation_requested=false WHERE id=%s',(parent,))
  c.execute("UPDATE hb_delivery SET state='cancelled' WHERE id=%s",(reply['id'],))
  peer=c.execute("SELECT * FROM hb_principal WHERE id='ba360a7a-f516-4b19-911f-b9513790e5ca'").fetchone()
  ordinary=store.event(c,peer,event['room'],'request','Rollback-only ordinary agent request',recipient=actor['id'])
  c.execute('INSERT INTO hb_delivery(id,authority,event,agent) VALUES(%s,%s,%s,%s)',(uuid4(),actor['authority'],ordinary,actor['id']))
  ordinary_view=diagnose(store,c,actor,h,CAPABILITIES);assert ordinary_view['eligibleDeliveryId'] is None and ordinary_view['queueClass']=='worker' and ordinary_view['reason']=='worker_capacity_reserved_for_control',ordinary_view
  c.execute("UPDATE hb_delivery SET state='cancelled' WHERE event=%s",(ordinary,));c.execute("UPDATE hb_delivery SET state='pending' WHERE id=%s",(reply['id'],))
  executor=c.execute("SELECT id FROM hb_executor WHERE agent=%s AND state='stopped' LIMIT 1",(actor['id'],)).fetchone()['id']
  c.execute("UPDATE hb_executor SET state='unknown' WHERE id=%s",(executor,));held=diagnose(store,c,actor,h,CAPABILITIES);assert held['eligibleDeliveryId'] is None
  result={'passed':True,'realPostgreSQLAndActualHostPolicy':True,'maximumSlots':1,'reservedControlSlots':1,'ordinaryWorkerSlots':0,'awaitedPeerReplyEligibleInControlLane':True,'senderRemainsAgent':True,'ordinaryPeerRequestCannotTakeReservedSlot':True,'parentCancellationRecheckedBeforeAdmission':True,'unsettledExecutorStillBlocks':True,'negativeFixtures':'SYNTHETIC rollback-only; no execution proof','modelsStarted':0,'databaseFixturesRolledBack':True}
 finally:c.rollback()
(r/'peer-control-admission-qualification.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
