"""Captured native Goal deadlock, real private PG/API; no model behavior claim."""
import copy
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from lax_connector.host import request
from lax_connector.context_runtime import CAPABILITIES
from hollywood_backbone.store import Store, Conflict, digest
from hollywood_backbone.context_goal_dependencies import refresh, wait
from hollywood_backbone.context_goal_review_frontier import native_parent

G="2d4a0daa-0514-475e-be1e-71decacbd5bc"
A="e488f19b-0f60-46ca-bd48-4fe544f06757"
SOURCE="dc2d4ba8-9d28-4514-b775-a51621577c67"
GOAL="23dbbd42-11c8-595d-8bfd-91055bb143b3"
PARENT="deffaaeb-d1c9-57d5-a8d4-69daecd4ce9d"
WORK="366230d9-9062-5c22-9b6c-d96859e6a6f0"
def save(p,v):p.write_text(json.dumps(v,indent=2,default=str)+"\n")
def one(c,table,id):return c.execute("SELECT * FROM "+table+" WHERE id=%s",(id,)).fetchone()

def main():
    os.umask(0o077)
    root=Path(os.environ["GOAL_FRONTIER_EVIDENCE"]);assert not root.exists();root.mkdir(mode=0o700)
    dbcfg=json.loads(Path(os.environ["GOAL_FRONTIER_DATABASE"]).read_text())
    assert dbcfg["dbname"]=="harness_goal_frontier_20261021_r285"
    m=json.loads((Path("/var/lib/harness")/A/"purpose-agents"/G/"installation.json").read_text())
    cfg=json.loads(Path(m["config"]).read_text())
    assert cfg["agentId"]==G
    writer=cfg["tokens"]["writer"];reader=cfg["tokens"]["reader"]
    url="http://127.0.0.1:18796"
    db=psycopg.connect(**dbcfg,row_factory=dict_row,autocommit=True)
    parent=one(db,"hb_context_material",PARENT)
    assert parent["state"]=="waiting" and str(parent["source"])==SOURCE
    original_goal=one(db,"hb_goal",GOAL);original_work=one(db,"hb_work",WORK)
    assert original_goal["state"]=="active" and original_work["state"]=="submitted"
    original_delivery=one(db,"hb_delivery",original_work["delivery"])
    assert original_delivery["state"]=="completed"
    binding=db.execute("SELECT * FROM hb_purpose_context WHERE agent=%s AND purpose='writer'",(G,)).fetchone()
    reader_binding=db.execute("SELECT * FROM hb_purpose_context WHERE agent=%s AND purpose='reader'",(G,)).fetchone()
    policy=db.execute("SELECT enabled FROM hb_portfolio_policy WHERE principal=%s",(G,)).fetchone()
    assert policy["enabled"]
    count=db.execute("SELECT count(*) AS n FROM hb_event").fetchone()["n"]
    save(root/"protocol.json",{"frozenAt":time.time(),"fixture":"Fresh consistent captured real production state, restored into private database; not a live-state repair",
        "sourceSha256":hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),"originalSource":SOURCE,
        "cases":["Old H87 authenticated pending leaves real submitted own Work deadlocked",
                 "H88 authenticated pending resumes exact parent once; original Goal/Work/deliveries unchanged, no event/self message",
                 "Repeated same frontier has no new resume revision",
                 "Already-actionable wait rejects before yielding",
                 "Assigned/running-only work and retired strategy do not wake",
                 "Unknown original delivery wakes for review without accepting/replaying",
                 "Other reviewer and cancelled parent do not grant review or revive work"],
        "providerModelRequests":0,"semanticProof":False})
    process=None
    def pending(purpose):
        b=binding if purpose=="writer" else reader_binding
        return request(url,writer if purpose=="writer" else reader,"POST",f"agents/{G}/contexts/{purpose}",
            {"runtimeProtocol":"lax-purpose-runtime/v3","operationId":str(uuid4()),"action":"pending",
             "nativeThread":b["native_thread"],"nativeTurn":"runtime","value":{"capabilities":CAPABILITIES}})
    def start(python,label):
        nonlocal process
        log=(root/(label+".log")).open("wb")
        process=subprocess.Popen([python,"-I","-B","-m","hollywood_backbone.server","--database-config",os.environ["GOAL_FRONTIER_DATABASE"],"--port","18796"],stdout=log,stderr=subprocess.STDOUT)
        log.close()
        for _ in range(80):
            if process.poll() is not None:raise RuntimeError(label+" server exited")
            try:return pending("writer")
            except Exception:time.sleep(.1)
        raise RuntimeError(label+" server did not answer")
    def stop():
        nonlocal process
        if process is not None:
            process.terminate();process.wait(timeout=20);process=None
    results=[]
    store=None
    try:
        old=start("/opt/harness/code-mode-material-20261021-r282/venv/bin/python","old-h87")
        assert PARENT not in {r["id"] for r in old["pending"]}
        assert one(db,"hb_context_material",PARENT)["state"]=="waiting"
        save(root/"old-control.json",{"state":"negative_control_reproduced","pending":old,"parent":one(db,"hb_context_material",PARENT)})
        results.append("old_h87_deadlock_reproduced");stop()
        fresh=start(os.environ.get("GOAL_FRONTIER_CANDIDATE_PYTHON","/opt/harness/native-goal-frontier-20261021-r285/venv/bin/python"),"candidate-h88")
        assert PARENT in {r["id"] for r in fresh["pending"]}
        after=one(db,"hb_context_material",PARENT)
        assert after["state"]=="pending" and after["value"]["resumeRevision"]==parent["value"].get("resumeRevision",0)+1
        assert one(db,"hb_goal",GOAL)==original_goal and one(db,"hb_work",WORK)==original_work
        assert one(db,"hb_delivery",original_work["delivery"])==original_delivery
        pending("reader");again=pending("writer")
        assert one(db,"hb_context_material",PARENT)==after
        assert db.execute("SELECT count(*) AS n FROM hb_event").fetchone()["n"]==count
        save(root/"candidate-api.json",{"pending":fresh,"secondPending":again,"parent":after,"sameGoalWorkDelivery":True,"newConversationEvents":0})
        results.append("authenticated_native_resume_once_no_self_message");stop()
        store=Store(dbcfg)
        def transaction_case(name,edit,expect):
            with store.pool.connection() as c:
                c.execute("UPDATE hb_context_material SET state=%s,value=%s,revision=%s WHERE id=%s",
                    (parent["state"],Jsonb(parent["value"]),parent["revision"],PARENT))
                actor=store.actor(c,G)
                edit(c,actor)
                refresh(c,store,actor)
                actual=one(c,"hb_context_material",PARENT)
                assert actual["state"]==expect,(name,actual["state"])
                save(root/(name+".json"),{"state":"passed","parentState":actual["state"],"fixtureCounterfactual":True,"productionWrites":0})
                c.rollback()
            results.append(name)
        def actionable_wait(c,actor):
            assert native_parent(c,G,original_goal)
            c.execute("UPDATE hb_context_material SET state='pending' WHERE id=%s",(PARENT,))
            row=one(c,"hb_context_material",PARENT);b=dict(binding)
            b["reads"]={PARENT:{"revision":row["revision"],"turn":"disclosed-machine-wait"}}
            try:wait(c,store,actor,b,{"nativeTurn":"disclosed-machine-wait","value":{"workId":PARENT,"goalIds":[GOAL]}})
            except Conflict as e:assert "review is actionable" in str(e)
            else:raise AssertionError("Actionable review was allowed to yield")
        transaction_case("actionable_wait_rejected",actionable_wait,"pending")
        def running(c,actor):
            c.execute("UPDATE hb_work SET state='assigned' WHERE id=%s",(WORK,))
            c.execute("UPDATE hb_delivery SET state='running' WHERE id=%s",(original_work["delivery"],))
        transaction_case("running_only_no_wake",running,"waiting")
        def retired(c,actor):
            prior=c.execute("SELECT * FROM hb_goal_strategy WHERE goal=%s AND revision=%s",(GOAL,original_goal["plan_revision"])).fetchone()
            body=copy.deepcopy(prior["strategy"]);body["frontierWorkIds"]=[]
            rev=prior["revision"]+1
            c.execute("INSERT INTO hb_goal_strategy(goal,revision,definition_revision,strategy,digest,actor,source) VALUES(%s,%s,%s,%s,%s,%s,%s)",
                (GOAL,rev,prior["definition_revision"],Jsonb(body),digest(body),prior["actor"],prior["source"]))
            c.execute("UPDATE hb_goal SET plan_revision=%s,revision=revision+1 WHERE id=%s",(rev,GOAL))
        transaction_case("retired_frontier_no_wake",retired,"waiting")
        def unknown(c,actor):c.execute("UPDATE hb_delivery SET state='unknown' WHERE id=%s",(original_work["delivery"],))
        transaction_case("unknown_requires_review",unknown,"pending")
        def other(c,actor):c.execute("UPDATE hb_goal SET owner=%s WHERE id=%s",("301c8b83-8cdb-5b9a-9d7f-3b2fa6f0b3f6",GOAL))
        transaction_case("other_reviewer_no_authority",other,"waiting")
        def cancelled(c,actor):c.execute("UPDATE hb_delivery SET cancellation_requested=true WHERE agent=%s AND event=%s",(G,SOURCE))
        transaction_case("cancelled_parent_not_revived",cancelled,"waiting")
        result={"at":time.time(),"state":"passed","cases":results,"source":SOURCE,"exactOriginalGoalWorkUnchanged":True,
            "newConversationEvents":0,"providerModelRequests":0,"businessEffects":0,"scope":"Actual authenticated Hollywood API/real PG captured-deadlock integration, plus disclosed transactional machine counterfactuals. Not semantic/model behavior or complete system proof."}
        save(root/"result.json",result);print(json.dumps(result),flush=True)
    except BaseException as e:
        save(root/"failure.json",{"at":time.time(),"error":repr(e),"completed":results,"expectedAnswersChanged":False});raise
    finally:
        stop()
        if store:store.close()
        db.close()

if __name__=="__main__":main()
