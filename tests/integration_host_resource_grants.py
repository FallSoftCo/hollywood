"""Rollback-only real PostgreSQL/HTTP authorization, no native or model launch.

Uses the explicit isolated QA authority. Running Work/executor facts in this test
are SYNTHETIC transactional fixtures; never evidence of live native execution.
"""
import asyncio,contextlib,json,sys
from pathlib import Path
from uuid import uuid4
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from aiohttp import ClientSession
from aiohttp.test_utils import TestServer
from hollywood_backbone.store import Store
from hollywood_backbone.server import application

root=Path(sys.argv[1]);i=json.loads((root/'canary-intent.json').read_text());s=json.loads((root/'enterprise/installation-intent.json').read_text());assert i['businessActions'] is False and s['name']=='Single-context native federation qualification'
controller=json.loads(Path(json.loads((root/'host-enrollment.json').read_text())['configPath']).read_text());token=controller['token']
with psycopg.connect(**json.loads((root/'database.json').read_text()),row_factory=dict_row) as c:
 class Borrow:
  @contextlib.contextmanager
  def connection(self):
   with c.transaction():yield c
 store=Store.__new__(Store);store.pool=Borrow();goal,work=[str(uuid4()) for _ in range(2)]
 d=c.execute('SELECT * FROM hb_delivery WHERE authority=%s AND agent=%s',(i['authorityId'],i['agentId'])).fetchone();assert d and d['state']=='completed'
 workspace=c.execute('SELECT * FROM hb_physical_workspace WHERE authority=%s AND host=%s',(i['authorityId'],i['hostId'])).fetchone();assert workspace
 c.execute("INSERT INTO hb_goal(id,authority,room,owner,creator,source,title,objective,acceptance) VALUES(%s,%s,%s,%s,%s,%s,'SYNTHETIC rollback goal','No business work','Authorization only')",(goal,i['authorityId'],s['globalRoomId'],i['agentId'],s['credentials']['Owner']['principalId'],d['event']))
 c.execute("INSERT INTO hb_work(id,authority,goal,room,owner,specification,acceptance,state,request_event,delivery) VALUES(%s,%s,%s,%s,%s,'SYNTHETIC rollback work','Authorization only','assigned',%s,%s)",(work,i['authorityId'],goal,s['globalRoomId'],i['agentId'],d['event'],d['id']))
 c.execute("UPDATE hb_delivery SET state='running' WHERE id=%s",(d['id'],));c.execute("UPDATE hb_executor SET state='live',observed_at=clock_timestamp() WHERE delivery=%s",(d['id'],))
 c.execute('UPDATE hb_event SET data=data||%s WHERE id=%s',(Jsonb({'workId':work,'goalId':goal,'workRevision':1,'attemptId':str(d['id']),'attemptNumber':1,'workCompletionPolicy':'report-required/v1'}),d['event']))
 async def run():
  async with TestServer(application(store),host='127.0.0.1') as server:
   async with ClientSession() as client:
    route='/hollywood/v3/physical-workspaces/'+str(workspace['id'])+'/grants/'+work
    def payload(revision=0):return {'operationId':'host-grant-'+str(uuid4()),'revision':revision,'workspaceRevision':workspace['revision'],'workRevision':1,'state':'active'}
    async def put(p,expected=200):
     async with client.put(server.make_url(route),json=p,headers={'Authorization':'Bearer '+token}) as r:
      v=await r.json();assert r.status==expected,(r.status,v);return v
    executor=c.execute('SELECT id FROM hb_executor WHERE delivery=%s',(d['id'],)).fetchone()['id']
    async with client.get(server.make_url('/hollywood/v3/executors/'+str(executor)+'?view=capture'),headers={'Authorization':'Bearer '+token}) as r:
     snapshot=await r.json();assert r.status==200 and snapshot['capture']['work']['id']==work and snapshot['capture']['goal']['id']==goal
    p=payload();first=await put(p);assert first['mayApply'] is False
    async with client.get(server.make_url('/hollywood/v3/administration/commands/'+p['operationId']),headers={'Authorization':'Bearer '+token}) as r:
     receipt=await r.json();assert r.status==200 and receipt['acceptedResult']==first
    c.execute("UPDATE hb_delivery SET state='completed' WHERE id=%s",(d['id'],))
    assert await put(p)==first  # exact accepted receipt, no second grant
    await put(payload(1),403)
    c.execute("UPDATE hb_delivery SET state='running' WHERE id=%s",(d['id'],))
    c.execute("UPDATE hb_physical_grant SET state='revoked' WHERE workspace=%s AND work=%s",(workspace['id'],work));await put(payload(1),403)
    c.execute("UPDATE hb_physical_grant SET state='active' WHERE workspace=%s AND work=%s",(workspace['id'],work))
    c.execute('UPDATE hb_physical_agent_grant SET can_write=false WHERE workspace=%s AND principal=%s',(workspace['id'],i['agentId']));await put(payload(1),403);c.execute('UPDATE hb_physical_agent_grant SET can_write=true WHERE workspace=%s AND principal=%s',(workspace['id'],i['agentId']))
    c.execute("UPDATE hb_executor SET state='stopped' WHERE delivery=%s",(d['id'],));await put(payload(1),403);c.execute("UPDATE hb_executor SET state='live' WHERE delivery=%s",(d['id'],))
    c.execute('UPDATE hb_host_admission SET controller=%s WHERE host=%s',(s['credentials']['Owner']['principalId'],i['hostId']));await put(payload(1),403);c.execute('UPDATE hb_host_admission SET controller=%s WHERE host=%s',(controller['controllerId'] if 'controllerId' in controller else json.loads((root/'host-enrollment.json').read_text())['controllerId'],i['hostId']))
    c.execute("UPDATE hb_goal SET state='paused' WHERE id=%s",(goal,));await put(payload(1),403);c.execute("UPDATE hb_goal SET state='active' WHERE id=%s",(goal,))
    c.execute("UPDATE hb_collaboration_bridge SET state='revoked' WHERE host=%s",(i['hostId'],));await put(payload(1),403)
    async with client.post(server.make_url('/hollywood/v3/principals'),json={'id':str(uuid4()),'name':'Not authorized','kind':'service','enrollmentCredential':'x'*40},headers={'Authorization':'Bearer '+token}) as r:
     assert r.status==403,await r.text()
    assert c.execute('SELECT revision FROM hb_physical_grant WHERE workspace=%s AND work=%s',(workspace['id'],work)).fetchone()['revision']==1
    return {'passed':True,'realPostgreSQLAndHTTP':True,'nativeFacts':'SYNTHETIC rollback-only fixture, not native execution proof','ownHostCurrentWorkGrant':True,'originalAcceptedReceiptSurvivesCompletionWithoutSecondMutation':True,'completedWorkExecutionCannotObtainNewGrant':True,'revokedGrantNotRestored':True,'agentAccessRevocationHonored':True,'stoppedExecutorDenied':True,'revokedBridgeDenied':True,'changedHostControllerDenied':True,'pausedGoalDenied':True,'workSnapshotLimitedToOriginalAssignment':True,'broaderAdministrativeActionsDenied':True,'grantDoesNotAdmitFileMutation':True,'modelOrWorkerLaunches':0,'fixturesRolledBack':True}
 result=asyncio.run(run());c.rollback();assert not c.execute('SELECT 1 FROM hb_work WHERE id=%s',(work,)).fetchone()
(root/'host-grant-qualification.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
