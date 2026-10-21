"""Real HTTP/PostgreSQL contract qualification; lifecycle fixtures are not model work."""
import asyncio,hashlib,json,os,secrets,sys,time
from pathlib import Path
from uuid import uuid4
from aiohttp import ClientSession,web
from hollywood_backbone.server import application
from hollywood_backbone.store import Store,digest
from psycopg.types.json import Jsonb

async def main():
    root=Path(os.environ["CONTRACT_EVIDENCE"]);root.mkdir(mode=0o700)
    def save(name,value):(root/name).write_text(json.dumps(value,indent=2,default=str)+"\n")
    os.umask(0o077)
    ids={k:str(uuid4()) for k in ["authority","room","goal","work","viewer","legacyGoal","legacyWork"]}
    credentials={k:{"principalId":str(uuid4()),"token":secrets.token_urlsafe(40)} for k in ["Owner","Global"]}
    viewer_token=secrets.token_urlsafe(40);tokens={k:v["token"] for k,v in credentials.items()};tokens["Viewer"]=viewer_token
    save("intent.json",{"at":time.time(),"ids":ids,"credentials":credentials,"viewerToken":viewer_token,
        "runnerSha256":hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "scope":"Real HTTP/PG transactions and revision/authorization/receipt guards. Explicit terminal-state SQL fixtures only to isolate review/coverage checks; no native execution or semantic model success claimed."})
    store=Store(json.loads(Path(os.environ["CONTRACT_DATABASE"]).read_text()));store.migrate()
    store.bootstrap(ids["authority"],"Finite Goal contract QA",{"globalRoomId":ids["room"],"credentials":credentials})
    checks=[];trace=[];runner=web.AppRunner(application(store));await runner.setup()
    port=int(os.environ.get("CONTRACT_PORT","18784"))
    site=web.TCPSite(runner,"127.0.0.1",port);await site.start()
    async with ClientSession() as client:
      async def call(method,path,body=None,who="Owner",status=200):
        async with client.request(method,"http://127.0.0.1:"+str(port)+"/hollywood/v3/"+path,json=body,
            headers={"Authorization":"Bearer "+tokens[who]}) as response:
          data=await response.json();trace.append({"method":method,"path":path,"who":who,"status":response.status,"response":data})
          save("trace.json",trace);assert response.status==status,(method,path,response.status,data)
          return data
      async def goal():
        return (await call("GET","goals/"+ids["goal"]))["goal"]
      async def contract(action,fields,status=200,who="Owner"):
        g=await goal()
        return await call("POST","goals/"+ids["goal"]+"/contract/"+action,
             {"operationId":"contract-r269:"+str(uuid4()),"revision":g["revision"],
              "sourceEventId":source["event"]["id"],"note":"Disclosed structural contract qualification",**fields},who,status)
      async def create_work(identity,criteria,status=200):
        g=await goal()
        return await call("POST","goals/"+ids["goal"]+"/work",{"operationId":"contract-work:"+str(uuid4()),
          "revision":g["revision"],"id":identity,"roomId":ids["room"],
          "ownerId":credentials["Global"]["principalId"],"specification":"Structural fixture only; do not execute",
          "acceptance":"Fixture criterion","dependencies":[],"criterionIds":criteria},status=status)
      try:
        await call("POST","principals",{"id":ids["viewer"],"name":"Unsubscribed viewer","kind":"human","enrollmentCredential":viewer_token},who="Owner")
        source=await call("POST","rooms/"+ids["room"]+"/messages",
          {"operationId":"contract-source-r269","kind":"request","body":"Finite API qualification only; no model starts","audience":"room","recipientId":credentials["Global"]["principalId"]},who="Owner")
        create={"operationId":"contract-goal-r269","id":ids["goal"],"sourceEventId":source["event"]["id"],
          "title":"Goal contract structural QA","objective":"Initial proposal","acceptance":"To be defined","reviewBudget":0,"requireDefinition":True}
        await call("POST","rooms/"+ids["room"]+"/goals",create)
        await create_work(ids["work"],["result"],409);checks.append("required definition blocks premature Work")
        await call("GET","goals/"+ids["goal"],who="Viewer",status=403);checks.append("unsubscribed human cannot read Goal contract")
        definition={"objective":"Produce the requested verified result","criteria":[{"id":"result","requirement":"Result is independently checked","method":"review","verification":"Reviewer inspects actual evidence"}],"assumptions":[],"openQuestions":["Which audience?"]}
        proposed=await contract("definition",{"definitionRevision":0,"definition":definition})
        await contract("adopt",{"definitionRevision":1,"definitionSha256":proposed["contract"]["definitionSha256"]},409)
        checks.append("unresolved recorded question blocks adoption")
        definition["openQuestions"]=[];definition["assumptions"]=["Internal audience"]
        proposed=await contract("definition",{"definitionRevision":1,"definition":definition})
        await contract("adopt",{"definitionRevision":2,"definitionSha256":"0"*64},409)
        adopted=await contract("adopt",{"definitionRevision":2,"definitionSha256":proposed["contract"]["definitionSha256"]},who="Owner")
        assert adopted["contract"]["reviewerKind"]=="human" and adopted["contract"]["reviewerId"]==credentials["Owner"]["principalId"]
        checks.append("exact definition digest and actual authenticated review identity")
        await create_work(str(uuid4()),["invented"],409);await create_work(ids["work"],["result"])
        work=(await call("GET","work/"+ids["work"]))["work"]
        await call("POST","work/"+ids["work"]+"/assign",{"operationId":"assign-before-plan","revision":work["revision"]},status=409)
        checks.append("Work criteria and current strategy required for assignment")
        strategy={"alternatives":[{"id":"direct","approach":"Build and verify the result","tradeoffs":"Shorter path"},{"id":"reuse","approach":"Inspect an existing result before building","tradeoffs":"May avoid duplicate work"}],
          "selected":"direct","rationale":"No accepted existing artifact","frontierWorkIds":[ids["work"]],"replanWhen":["Source requirement changes"]}
        plan=await contract("strategy",{"planRevision":0,"definitionRevision":2,"strategy":strategy})
        work=(await call("GET","work/"+ids["work"]))["work"]
        assigned=await call("POST","work/"+ids["work"]+"/assign",{"operationId":"assign-current-plan","revision":work["revision"]})
        work=(await call("GET","work/"+ids["work"]))["work"]
        assert work["goal_definition_revision"]==2 and work["goal_plan_revision"]==1 and work["criterion_ids"]==["result"]
        checks.append("original assignment captures definition/strategy/criteria")
        # Deliberate synthetic terminal state isolates review guards. No executor/native success is asserted.
        with store.pool.connection() as c:
          c.execute("UPDATE hb_delivery SET state='completed',native_thread='structural-fixture-not-model',native_turn='structural-fixture-not-model' WHERE id=%s",(assigned["deliveryId"],))
          c.execute("UPDATE hb_work SET state='submitted' WHERE id=%s",(ids["work"],))
        retired=str(uuid4());await create_work(retired,["result"])
        changed={**definition,"criteria":[{"id":"privacy","requirement":"New privacy criterion","method":"review","verification":"Inspect aggregate-only result"}]}
        g=await goal();epoch=g["control_revision"]
        proposed=await contract("definition",{"definitionRevision":2,"definition":changed})
        await contract("adopt",{"definitionRevision":3,"definitionSha256":proposed["contract"]["definitionSha256"]})
        assert (await goal())["control_revision"]>epoch
        old=(await call("GET","work/"+ids["work"]))["work"]
        denied=await call("POST","work/"+ids["work"]+"/review",{"operationId":"old-evidence-accept","revision":old["revision"],"decision":"accepted","note":"This must fail for the definition mismatch"},status=409)
        assert denied["code"]=="WORK_DEFINITION_SUPERSEDED"
        checks.append("changed adopted definition fences old acceptance and Goal control epoch")
        # Accepted-old-state fixture proves coverage cannot be satisfied by stale Work.
        with store.pool.connection() as c:c.execute("UPDATE hb_work SET state='accepted' WHERE id=%s",(ids["work"],))
        g=await goal()
        rejected=await call("POST","goals/"+ids["goal"]+"/state",{"operationId":"stale-goal-accept","revision":g["revision"],"state":"accepted","note":"Old work must not satisfy changed criteria"},status=409)
        assert rejected["code"]=="GOAL_STRATEGY_REQUIRED"
        strategy["frontierWorkIds"]=[]
        await contract("strategy",{"planRevision":1,"definitionRevision":3,"strategy":strategy})
        g=await goal()
        rejected=await call("POST","goals/"+ids["goal"]+"/state",{"operationId":"missing-coverage-accept","revision":g["revision"],"state":"accepted","note":"Must lack current criterion evidence"},status=409)
        assert rejected["code"]=="GOAL_CRITERIA_UNCOVERED"
        checks.append("old strategy and old accepted Work cannot satisfy current Goal")
        fresh=str(uuid4());await create_work(fresh,["privacy"]);strategy["frontierWorkIds"]=[fresh]
        await contract("strategy",{"planRevision":2,"definitionRevision":3,"strategy":strategy})
        w=(await call("GET","work/"+fresh))["work"]
        current_assignment=await call("POST","work/"+fresh+"/assign",{"operationId":"assign-current-revision","revision":w["revision"]})
        with store.pool.connection() as c:
          c.execute("UPDATE hb_delivery SET state='completed',native_thread='structural-fixture-not-model',native_turn='structural-fixture-not-model' WHERE id=%s",(current_assignment["deliveryId"],))
          c.execute("UPDATE hb_work SET state='submitted' WHERE id=%s",(fresh,))
        w=(await call("GET","work/"+fresh))["work"]
        await call("POST","work/"+fresh+"/review",{"operationId":"review-current-revision","revision":w["revision"],"decision":"accepted","note":"Synthetic-state review guard only; not a real product review"})
        g=await goal()
        unsettled=await call("POST","goals/"+ids["goal"]+"/state",{"operationId":"reject-unsettled-retired-path","revision":g["revision"],"state":"accepted","note":"Current criteria covered, but old planned path is not settled"},status=409)
        assert unsettled["code"]=="GOAL_WORK_UNSETTLED"
        retired_work=(await call("GET","work/"+retired))["work"]
        await call("POST","work/"+retired+"/cancel",{"operationId":"cancel-retired-path","revision":retired_work["revision"],"note":"Retire obsolete unstarted path after definition revision; no execution occurred"})
        checks.append("explicitly cancelled superseded path is preserved without blocking verified current completion")
        g=await goal()
        await call("POST","goals/"+ids["goal"]+"/state",{"operationId":"accept-covered-current-goal","revision":g["revision"],"state":"accepted","note":"Current-version structural fixture coverage"})
        checks.append("current typed bindings and complete criterion coverage permit acceptance")
        original=await call("GET","commands/"+create["operationId"])
        assert original["canonicalCommand"]["acceptedResult"]["goalId"]==ids["goal"] and original["canonicalCommand"]["acceptedResult"]["goalRevision"]==1
        checks.append("original accepted receipt remains unchanged after definition/plan updates")
        # Original pre-contract API payloads remain valid and deduplicated.
        legacy_create={**create,"id":ids["legacyGoal"],"operationId":"legacy-create-r269"}
        legacy_create.pop("requireDefinition")
        legacy=await call("POST","rooms/"+ids["room"]+"/goals",legacy_create)
        assert (await call("POST","rooms/"+ids["room"]+"/goals",legacy_create))==legacy
        legacy_goal=(await call("GET","goals/"+ids["legacyGoal"]))["goal"]
        assert not legacy_goal["contract_required"]
        await call("POST","goals/"+ids["legacyGoal"]+"/work",{
            "operationId":"legacy-work-r269","revision":legacy_goal["revision"],"id":ids["legacyWork"],
            "roomId":ids["room"],"ownerId":credentials["Global"]["principalId"],
            "specification":"Original API metadata only","acceptance":"Original acceptance","dependencies":[]})
        legacy_work=(await call("GET","work/"+ids["legacyWork"]))["work"]
        await call("POST","work/"+ids["legacyWork"]+"/assign",{
            "operationId":"legacy-assign-r269","revision":legacy_work["revision"]})
        checks.append("legacy Goal/Work API payloads and original creation dedup remain compatible")
        save("result.json",{"at":time.time(),"checks":checks,"passed":True,"modelsStarted":0,"lifecycleFixtures":"Explicit SQL terminal-state projections; not actual model or filesystem execution","liveProductionChanged":False})
        print(json.dumps({"passed":True,"checks":len(checks),"modelsStarted":0,"evidence":str(root)}))
      finally:
        await runner.cleanup();store.close()

if __name__=="__main__":asyncio.run(main())
