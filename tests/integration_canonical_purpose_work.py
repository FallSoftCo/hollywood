"""HTTP/PG canonical wait/review mechanisms; rollback-only metadata fixtures."""
import asyncio,contextlib,json,time
from pathlib import Path
from uuid import uuid4,UUID
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from aiohttp import ClientSession
from aiohttp.test_utils import TestServer
from hollywood_backbone.store import Store
from hollywood_backbone.server import application
from lax_connector.native_tool_catalog import CAPABILITIES
base=Path('/var/lib/lax-three-context-20261018-r01');qual=Path('/aosp/development/lax-three-context-20261018-r01/qualification')
out=qual/'canonical-purpose-work-http-r36.json'
if out.exists():raise RuntimeError('Inspect original contract result')
s=json.loads((base/'setup-intent.json').read_text());authority=s['authorityId'];agent=s['credentials']['Global']['principalId']
reviewer='3df5ce3f-334d-5942-b99b-84fe7436bd19';room='c5cf33ac-b91b-53a6-82c8-284e4a9dd605';host=s['hostId']
def config(a):return json.loads((Path('/var/lib/harness')/authority/'purpose-agents'/a/'home/agent.json').read_text())
cfg=config(agent);rcfg=config(reviewer)
proof={'schema':'lax-canonical-purpose-work-http/v1','at':time.time(),'mode':'Real authenticated HTTP and PostgreSQL; synthetic material/Goal/review metadata, all rolled back. No native launch, executor, permit or kernel-execution facts inserted. This is not a model cooperation proof.','modelsStarted':0}
with psycopg.connect(**json.loads((base/'database.json').read_text()),row_factory=dict_row) as c:
 class Borrow:
  @contextlib.contextmanager
  def connection(self):
   with c.transaction():yield c
 store=Store.__new__(Store);store.pool=Borrow()
 human=store.actor(c,s['credentials']['Owner']['principalId']);actor=store.actor(c,agent)
 source=store.event(c,human,room,'request','Rollback-only canonical prerequisite contract',recipient=agent)
 work,delivery,goal,unrelated=[str(uuid4()) for _ in range(4)]
 c.execute("INSERT INTO hb_delivery(id,authority,event,agent,state,generation,claim_id,host) VALUES(%s,%s,%s,%s,'claimed',1,'metadata-fixture',%s)",(delivery,authority,source,agent,host))
 c.execute("INSERT INTO hb_context_material(id,agent,kind,source,state,value) VALUES(%s,%s,'work',%s,'running',%s)",(work,agent,source,Jsonb({'objective':'Fixture only','execution':{'resumeRevision':0,'status':'completed'}})))
 for gid,origin in [(goal,source),(unrelated,str(uuid4()))]:
  if gid==unrelated:origin=store.event(c,human,room,'request','Other unrelated rollback-only source',recipient=agent)
  c.execute("INSERT INTO hb_goal(id,authority,room,owner,creator,source,title,objective,acceptance,review_remaining) VALUES(%s,%s,%s,%s,%s,%s,'Fixture Goal','Fixture','Fixture',3)",(gid,authority,room,agent,agent,origin))
 binding=c.execute("SELECT * FROM hb_purpose_context WHERE agent=%s AND purpose='writer'",(agent,)).fetchone()
 thread=binding['native_thread'];turn='rollback-metadata-not-native-execution'
 c.execute("UPDATE hb_purpose_context SET active_turn=%s,accepting=true,active_material=%s::uuid[],reads=%s WHERE agent=%s AND purpose='writer'",(turn,[work],Jsonb({work:{'revision':1,'turn':turn}}),agent))
 async def run():
  async with TestServer(application(store),host='127.0.0.1') as server:
   async with ClientSession() as client:
    async def call(action,value=None,who=agent,purpose='writer'):
     selected=cfg if who==agent else rcfg
     th=c.execute("SELECT native_thread FROM hb_purpose_context WHERE agent=%s AND purpose=%s",(who,purpose)).fetchone()['native_thread']
     p={'runtimeProtocol':'lax-purpose-runtime/v3','operationId':'qa-goal:'+str(uuid4()),'action':action,'value':value or {},'nativeThread':th,'nativeTurn':turn}
     async with client.post(server.make_url('/hollywood/v3/agents/'+who+'/contexts/'+purpose),json=p,headers={'Authorization':'Bearer '+selected['tokens'][purpose]}) as response:return response.status,await response.json()
    status,v=await call('wait_goals',{'workId':work,'goalIds':[unrelated]});assert status==403,(status,v)
    proof['unrelatedGoalRejectedEvenForAdministrator']=True
    status,v=await call('wait_goals',{'workId':work,'goalIds':[goal]});assert status==200 and v['state']=='waiting',(status,v)
    # End-of-inference consumption is only material metadata in this fixture.
    c.execute("UPDATE hb_purpose_context SET active_turn=NULL,accepting=false,active_material='{}' WHERE agent=%s AND purpose='writer'",(agent,))
    status,v=await call('pending');assert status==200 and work not in [x['id'] for x in v['pending']],(status,v)
    c.execute("UPDATE hb_goal SET revision=revision+1 WHERE id=%s",(goal,))
    status,v=await call('pending');assert work not in [x['id'] for x in v['pending']]
    proof['nonterminalGoalProgressDoesNotWakeWriter']=True
    from hollywood_backbone.context_goal_dependencies import require_resolved
    current=c.execute("SELECT * FROM hb_context_material WHERE id=%s",(work,)).fetchone()
    from hollywood_backbone.store import Conflict
    try:require_resolved(c,store,actor,current)
    except Conflict:pass
    else:raise AssertionError('Premature completion admitted')
    proof['unresolvedCanonicalGoalBlocksContextCompletion']=True
    # This terminal state is a synthetic projection, not claimed independent acceptance.
    c.execute("UPDATE hb_goal SET state='accepted',revision=revision+1 WHERE id=%s",(goal,))
    status,v=await call('pending');assert status==200 and work in [x['id'] for x in v['pending']],(status,v)
    current=c.execute("SELECT * FROM hb_context_material WHERE id=%s",(work,)).fetchone()
    assert current['state']=='pending' and current['value']['resumeRevision']==1
    rev=current['revision'];status,v=await call('pending')
    assert c.execute("SELECT revision FROM hb_context_material WHERE id=%s",(work,)).fetchone()['revision']==rev
    proof['exactTerminalGoalResumesSameWorkOnceWithoutNewInput']=True
    # No explicit wait tool: canonical source binding still prevents early completion
    # and a terminal Goal result can resume a consumed original responsibility.
    c.execute("UPDATE hb_context_material SET state='pending',value=%s WHERE id=%s",(Jsonb({'objective':'Fixture same source','execution':{'resumeRevision':0,'status':'completed'}}),work))
    status,v=await call('pending')
    current=c.execute("SELECT * FROM hb_context_material WHERE id=%s",(work,)).fetchone()
    assert current['value']['resumeRevision']==1
    proof['canonicalSourceContinuationDoesNotDependOnRememberingWaitTool']=True
    # Restore this fixture Goal to active for bounded domain review dispatch.
    c.execute("UPDATE hb_goal SET state='active',review_remaining=3 WHERE id=%s",(goal,))
    c.execute("INSERT INTO hb_goal_cooperation(authority,goal,policy,planner,reviewer) VALUES(%s,%s,'leader_award',%s,%s)",(authority,goal,agent,reviewer))
    c.execute("INSERT INTO hb_cooperation_executor VALUES(%s,%s,%s)",(authority,goal,agent))
    request=store.event(c,actor,room,'request','Rollback-only assigned work',recipient=agent,data={'goalId':goal,'workId':str(uuid4())})
    execution,formal=str(uuid4()),str(uuid4())
    report=store.event(c,actor,room,'progress','Rollback-only report',source=request,data={'goalId':goal,'workflowAction':'work.report'})
    c.execute("INSERT INTO hb_delivery(id,authority,event,agent,state,result_event) VALUES(%s,%s,%s,%s,'completed',%s)",(execution,authority,request,agent,report))
    c.execute("INSERT INTO hb_work(id,authority,goal,room,owner,specification,acceptance,state,request_event,delivery,report_event) VALUES(%s,%s,%s,%s,%s,'Fixture','Fixture','submitted',%s,%s,%s)",(formal,authority,goal,room,agent,request,execution,report))
    status,v=await call('pending',{'capabilities':CAPABILITIES},who=reviewer,purpose='reader');assert status==200,(status,v)
    g=c.execute("SELECT * FROM hb_goal WHERE id=%s",(goal,)).fetchone()
    assert g['review_request'] and g['review_remaining']==2
    event=c.execute("SELECT * FROM hb_event WHERE id=%s",(g['review_request'],)).fetchone()
    assert str(event['author'])==reviewer and event['data']['authorKind']=='agent' and event['data']['goalReview']
    assert c.execute("SELECT 1 FROM hb_context_material WHERE agent=%s AND source=%s AND kind='input' AND state='pending'",(reviewer,g['review_request'])).fetchone()
    assert c.execute("SELECT 1 FROM hb_delivery WHERE agent=%s AND event=%s AND state='pending'",(reviewer,g['review_request'])).fetchone()
    proof['existingBoundedReviewPolicyCreatesTypedAgentInputForActualReviewer']=True
    status,v=await call('pending',{'capabilities':CAPABILITIES},who=reviewer,purpose='reader');assert status==200
    after=c.execute("SELECT review_request,review_remaining FROM hb_goal WHERE id=%s",(goal,)).fetchone()
    assert after['review_request']==g['review_request'] and after['review_remaining']==2
    proof['unchangedReviewStateCreatesNoDuplicateRequestOrBudgetUse']=True
 asyncio.run(run())
 c.rollback()
 assert not c.execute("SELECT 1 FROM hb_context_material WHERE id=%s",(work,)).fetchone()
 assert not c.execute("SELECT 1 FROM hb_goal WHERE id=ANY(%s::uuid[])",([goal,unrelated],)).fetchone()
proof['allFixturesRolledBack']=True
out.write_text(json.dumps(proof,indent=2)+'\n');print(json.dumps(proof))

