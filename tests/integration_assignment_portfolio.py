"""Real HTTP/PG assignment + portfolio mechanics; all fixtures roll back."""
import asyncio,contextlib,json,time
from pathlib import Path
from uuid import uuid4
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from aiohttp import ClientSession
from aiohttp.test_utils import TestServer
from hollywood_backbone.store import Store
from hollywood_backbone.server import application
from lax_connector.native_tool_catalog import CAPABILITIES
base=Path('/var/lib/lax-three-context-20261018-r01');qual=Path('/aosp/development/lax-three-context-20261018-r01/qualification')
out=qual/'assignment-portfolio-http-r38.json'
if out.exists():raise RuntimeError('Inspect original qualification outcome')
s=json.loads((base/'setup-intent.json').read_text());authority=s['authorityId'];agent=s['credentials']['Global']['principalId'];host=s['hostId'];room='c5cf33ac-b91b-53a6-82c8-284e4a9dd605';reviewer='3df5ce3f-334d-5942-b99b-84fe7436bd19'
cfg=json.loads((Path('/var/lib/harness')/authority/'purpose-agents'/agent/'home/agent.json').read_text())
proof={'schema':'lax-assignment-portfolio-http/v1','at':time.time(),'mode':'Actual authenticated HTTP and PostgreSQL. Rollback-only Goal/delivery/material metadata fixtures; no native executor, launch, permit or process facts inserted. No model proof claimed.','modelsStarted':0}
with psycopg.connect(**json.loads((base/'database.json').read_text()),row_factory=dict_row) as c:
 class Borrow:
  @contextlib.contextmanager
  def connection(self):
   with c.transaction():yield c
 store=Store.__new__(Store);store.pool=Borrow()
 owner=store.actor(c,s['credentials']['Owner']['principalId']);actor=store.actor(c,agent);peer=store.actor(c,reviewer)
 source=store.event(c,owner,room,'request','Rollback-only assignment and supervision fixture',recipient=agent)
 gid,wid,other,otherSource=[str(uuid4()) for _ in range(4)]
 originalTurn=c.execute("SELECT active_turn FROM hb_purpose_context WHERE agent=%s AND purpose='writer'",(agent,)).fetchone()['active_turn']
 async def run():
  async with TestServer(application(store),host='127.0.0.1') as server:
   async with ClientSession() as client:
    async def http(method,path,value=None,token=None):
     async with client.request(method,server.make_url('/hollywood/v3/'+path),json=value,headers={'Authorization':'Bearer '+(token or s['credentials']['Owner']['token'])}) as response:return response.status,await response.json()
    async def pending():
     b=c.execute("SELECT native_thread FROM hb_purpose_context WHERE agent=%s AND purpose='reader'",(agent,)).fetchone()
     return await http('POST','agents/'+agent+'/contexts/reader',{'runtimeProtocol':'lax-purpose-runtime/v3','operationId':'qa-observe:'+str(uuid4()),'action':'pending','nativeThread':b['native_thread'],'nativeTurn':'metadata-observation','value':{'capabilities':CAPABILITIES}},cfg['tokens']['reader'])
    status,value=await http('POST','rooms/'+room+'/goals',{'operationId':'qa-goal:'+str(uuid4()),'id':gid,'sourceEventId':source,'title':'Fixture only','objective':'Fixture','acceptance':'Fixture','reviewBudget':2})
    assert status==200,(status,value)
    status,value=await http('POST','goals/'+gid+'/work',{'operationId':'qa-work:'+str(uuid4()),'id':wid,'revision':1,'ownerId':agent,'roomId':room,'specification':'Rollback-only assignment fixture','acceptance':'Fixture','dependencies':[]})
    assert status==200,(status,value)
    work=value
    status,value=await http('POST','work/'+wid+'/assign',{'operationId':'qa-assign:'+str(uuid4()),'revision':work['workRevision']})
    assert status==200,(status,value)
    d=value['deliveryId'];event=value['requestEventId']
    assert c.execute("SELECT 1 FROM hb_context_material WHERE id=%s AND agent=%s AND kind='input' AND state='pending'",(event,agent)).fetchone()
    proof['canonicalAssignmentAtomicallyIngestsOriginalRequest']=True
    canonical=c.execute("SELECT author,data FROM hb_event WHERE id=%s",(event,)).fetchone()
    assert canonical['author']==owner['id'] and canonical['data']['authorKind']=='human'
    # Reproduce the historical missing-input condition only on this fixture.
    c.execute("DELETE FROM hb_context_material WHERE source=%s AND agent=%s",(event,agent))
    b=c.execute("SELECT purpose,context_id,token_digest FROM hb_purpose_context WHERE agent=%s ORDER BY purpose",(agent,)).fetchall()
    conf={'operationId':'qa-reconcile:'+str(uuid4()),'hostId':host,'workspaceId':cfg['workspaceId'],'bindings':[{'purpose':x['purpose'],'contextId':str(x['context_id']),'credentialDigest':x['token_digest']} for x in b],'reconcilePendingDeliveryIds':[d]}
    status,value=await http('PUT','agents/'+agent+'/context-system',conf)
    assert status==200 and value['pendingInputReconciliation']['deliveries'][0]['inputAdded'],(status,value)
    status,cached=await http('PUT','agents/'+agent+'/context-system',conf);assert status==200 and cached==value
    assert c.execute("SELECT count(*) AS n FROM hb_context_material WHERE source=%s AND agent=%s AND kind='input'",(event,agent)).fetchone()['n']==1
    ds=c.execute("SELECT state,generation,claim_id,native_turn FROM hb_delivery WHERE id=%s",(d,)).fetchone()
    assert ds=={'state':'pending','generation':0,'claim_id':None,'native_turn':None}
    proof['exactOriginalPendingRepairIsIdempotentAndAdmitsNoExecution']=True
    c.execute("UPDATE hb_delivery SET state='cancelled',cancellation_requested=true WHERE id=%s",(d,))
    rejected={**conf,'operationId':'qa-rejected:'+str(uuid4())}
    status,value=await http('PUT','agents/'+agent+'/context-system',rejected);assert status==409,(status,value)
    proof['CancelledOriginalCannotBeRepairedOrRestarted']=True
    # Fresh activation only; actual current policy remains absent outside rollback.
    status,v=await http('PUT','agents/'+agent+'/supervision',{'operationId':'qa-policy:'+str(uuid4()),'revision':0,'roomId':s['globalRoomId'],'enabled':True,'reviewsPerWindow':2,'windowSeconds':60})
    assert status==200,(status,v)
    # Existing original purpose responsibility covers this exact terminal Goal.
    parent=str(uuid4());parentDelivery=str(uuid4())
    c.execute("INSERT INTO hb_delivery(id,authority,event,agent) VALUES(%s,%s,%s,%s)",(parentDelivery,authority,source,agent))
    c.execute("INSERT INTO hb_context_material(id,agent,kind,source,state,value) VALUES(%s,%s,'work',%s,'waiting',%s)",(parent,agent,source,Jsonb({'objective':'Metadata fixture','execution':{'resumeRevision':0,'status':'completed'}})))
    c.execute("UPDATE hb_goal SET state='accepted' WHERE id=%s",(gid,))
    accepted=store.event(c,peer,room,'control','Rollback-only terminal projection',source=source,data={'workflowAction':'goal.state','goalId':gid})
    status,v=await pending();assert status==200,(status,v)
    item=c.execute("SELECT * FROM hb_portfolio_item WHERE principal=%s AND reference=%s",(agent,gid)).fetchone()
    assert item and str(item['request_event'])==source and item['dispatched_seq']==item['latest_seq']
    assert c.execute("SELECT reviews_used FROM hb_portfolio_policy WHERE principal=%s",(agent,)).fetchone()['reviews_used']==0
    proof['terminalCausalGoalReusesOriginalResponsibilityWithoutDuplicateSelfReview']=True
    # A distinct new terminal obligation has no existing purpose responsibility.
    origin=store.event(c,owner,room,'request','Other fixture original',recipient=agent)
    c.execute("INSERT INTO hb_goal(id,authority,room,owner,creator,source,title,objective,acceptance,state) VALUES(%s,%s,%s,%s,%s,%s,'Other fixture','Fixture','Fixture','accepted')",(other,authority,room,agent,agent,origin))
    store.event(c,peer,room,'control','Other terminal fixture',source=origin,data={'workflowAction':'goal.state','goalId':other})
    status,v=await pending();assert status==200,(status,v)
    item=c.execute("SELECT * FROM hb_portfolio_item WHERE principal=%s AND reference=%s",(agent,other)).fetchone()
    assert item and item['request_event']
    request=c.execute("SELECT * FROM hb_event WHERE id=%s",(item['request_event'],)).fetchone()
    assert str(request['author'])==agent and request['data']['authorKind']=='agent' and request['data']['portfolioReview']
    assert c.execute("SELECT 1 FROM hb_context_material WHERE id=%s AND agent=%s AND kind='input' AND state='pending'",(request['id'],agent)).fetchone()
    assert c.execute("SELECT reviews_used FROM hb_portfolio_policy WHERE principal=%s",(agent,)).fetchone()['reviews_used']==1
    proof['newActionablePortfolioObligationCreatesTypedAgentInputInSameTransaction']=True
    status,v=await pending();assert status==200
    unchanged=c.execute("SELECT request_event FROM hb_portfolio_item WHERE principal=%s AND reference=%s",(agent,other)).fetchone()
    assert unchanged['request_event']==request['id']
    assert c.execute("SELECT reviews_used FROM hb_portfolio_policy WHERE principal=%s",(agent,)).fetchone()['reviews_used']==1
    proof['unchangedOrUnsettledReviewCreatesNoSecondRequestOrAcknowledgement']=True
    status,v=await http('PUT','agents/'+agent+'/supervision',{'operationId':'qa-disable:'+str(uuid4()),'revision':1,'roomId':s['globalRoomId'],'enabled':False,'reviewsPerWindow':2,'windowSeconds':60});assert status==200,(status,v)
    status,v=await pending();assert status==200
    assert c.execute("SELECT reviews_used FROM hb_portfolio_policy WHERE principal=%s",(agent,)).fetchone()['reviews_used']==1
    proof['disabledSupervisionRemainsDisabled']=True
 asyncio.run(run())
 c.rollback()
 assert not c.execute("SELECT 1 FROM hb_goal WHERE id=ANY(%s::uuid[])",([gid,other],)).fetchone()
 assert c.execute("SELECT active_turn FROM hb_purpose_context WHERE agent=%s AND purpose='writer'",(agent,)).fetchone()['active_turn']==originalTurn
proof['allFixturesRolledBack']=True
proof['liveNativeWriterBindingPreserved']=True
out.write_text(json.dumps(proof,indent=2)+'\n');print(json.dumps(proof))

