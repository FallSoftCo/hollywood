"""Real HTTP/PG recovery fences on a stopped isolated QA reservation; rollback."""
import asyncio,contextlib,json,sys
from pathlib import Path
from uuid import uuid4
import psycopg
from psycopg.rows import dict_row
from aiohttp import ClientSession
from aiohttp.test_utils import TestServer
from hollywood_backbone.server import application
from hollywood_backbone.store import Store

r=Path(sys.argv[1]);i=json.loads((r/'enterprise/installation-intent.json').read_text());assert i['name']=='Single-context native federation qualification'
delivery='125c3c22-4811-4c9d-8688-ae5a9b7b0643'
with psycopg.connect(**json.loads((r/'database.json').read_text()),row_factory=dict_row) as c:
 c.execute('BEGIN')
 class Borrow:
  @contextlib.contextmanager
  def connection(self):
   with c.transaction():yield c
 store=Store.__new__(Store);store.pool=Borrow()
 d=c.execute('SELECT * FROM hb_delivery WHERE id=%s',(delivery,)).fetchone();e=c.execute('SELECT * FROM hb_executor WHERE delivery=%s ORDER BY generation DESC LIMIT 1',(delivery,)).fetchone()
 assert d['state']=='unknown' and e['state']=='stopped' and e['launch_phase']=='reserved' and e['descriptor']['nativePid'] is None
 async def run():
  async with TestServer(application(store),host='127.0.0.1') as server:
   async with ClientSession() as client:
    def payload():return {'schema':'hollywood-pre-native-recovery/v1','operationId':'rollback-reserved-'+str(uuid4()),'revision':d['revision'],'generation':d['generation'],'resultEventId':None,'executorId':str(e['id']),'outcome':'native_launch_not_dispatched','reconciliationSha256':'a'*64,'note':'Rollback-only authorization fixture; not a live recovery attestation'}
    async def post(p,status,token=None):
     async with client.post(server.make_url('/hollywood/v3/deliveries/'+delivery+'/recover-before-native'),json=p,headers={'Authorization':'Bearer '+(token or i['credentials']['Owner']['token'])}) as resp:
      v=await resp.json();assert resp.status==status,(resp.status,v);return v
    await post({k:v for k,v in payload().items() if k!='reconciliationSha256'},400)
    await post(payload(),403,token=json.loads(Path(json.loads((r/'host-enrollment.json').read_text())['configPath']).read_text())['token'])
    c.execute("UPDATE hb_executor SET state='unknown' WHERE id=%s",(e['id'],));await post(payload(),409)
    c.execute("UPDATE hb_executor SET state='stopped',launch_phase='attached' WHERE id=%s",(e['id'],));await post(payload(),409)
    c.execute("UPDATE hb_executor SET launch_phase='reserved' WHERE id=%s",(e['id'],))
    c.execute('UPDATE hb_delivery SET cancellation_requested=true WHERE id=%s',(delivery,));await post(payload(),409)
    c.execute('UPDATE hb_delivery SET cancellation_requested=false WHERE id=%s',(delivery,))
    prior=c.execute('SELECT * FROM hb_executor WHERE delivery=%s AND id<>%s',(delivery,e['id'])).fetchone();assert prior
    saved=c.execute('SELECT before_snapshot FROM hb_delivery_recovery WHERE delivery=%s AND generation=%s',(delivery,prior['generation'])).fetchone()['before_snapshot']
    c.execute("UPDATE hb_delivery_recovery SET before_snapshot=before_snapshot - 'preNativeReconciliation' WHERE delivery=%s AND generation=%s",(delivery,prior['generation']));await post(payload(),409)
    from psycopg.types.json import Jsonb
    c.execute('UPDATE hb_delivery_recovery SET before_snapshot=%s WHERE delivery=%s AND generation=%s',(Jsonb(saved),delivery,prior['generation']))
    c.execute("UPDATE hb_executor SET launch_phase='attached' WHERE id=%s",(prior['id'],));await post(payload(),409)
    c.execute("UPDATE hb_executor SET launch_phase='reserved' WHERE id=%s",(prior['id'],))
    p=payload();first=await post(p,200);assert first['state']=='pending' and first['originalAttemptPreserved'] and first['explicitReservedLaunchReconciliation']
    assert await post(p,200)==first
    assert c.execute('SELECT count(*) AS n FROM hb_delivery_recovery WHERE delivery=%s',(delivery,)).fetchone()['n']==3
    proof=c.execute('SELECT before_snapshot FROM hb_delivery_recovery WHERE delivery=%s ORDER BY generation DESC LIMIT 1',(delivery,)).fetchone()['before_snapshot']['preNativeReconciliation'];assert proof['executor']['id']==str(e['id'])
    return {'passed':True,'realPostgreSQLAndHTTP':True,'explicitAdministratorAttestationRequired':True,'exactStoppedReservedOriginalExecutorRequired':True,'servicePrincipalDenied':True,'attachedOrUnsettledLaunchDenied':True,'cancellationPreserved':True,'originalRequestAndAttemptPreserved':True,'acceptedOperationReplaysReceiptOnly':True,'databaseFixturesRolledBack':True,'modelsStarted':0}
 try:result=asyncio.run(run())
 finally:c.rollback()
(r/'repeated-reserved-launch-recovery-qualification.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
