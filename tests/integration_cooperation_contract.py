"""Actual PG/authenticated owner API cooperation contract; all mutations roll back."""
import argparse, asyncio, contextlib, json, time
from pathlib import Path
from uuid import uuid4
import psycopg
from psycopg.rows import dict_row
from aiohttp import ClientSession
from aiohttp.test_utils import TestServer
from hollywood_backbone.server import application
from hollywood_backbone.store import Store

def main(a):
    assert not a.output.exists(), "Inspect original receipt"
    proof={"at":time.time(),"mode":a.mode,"modelsStarted":0,
           "scope":"Actual PG and authenticated QA Owner HTTP, rollback-only metadata; not agent/model execution.",
           "cases":[]}
    with psycopg.connect(**json.loads(a.database.read_text()),row_factory=dict_row) as c:
        class Borrow:
            @contextlib.contextmanager
            def connection(self):
                with c.transaction():yield c
        store=Store.__new__(Store);store.pool=Borrow()
        setup=json.loads(a.bootstrap.read_text());token=setup["credentials"]["Owner"]["token"]
        goal=c.execute("SELECT * FROM hb_goal WHERE id=%s",(a.goal,)).fetchone()
        assert goal and not c.execute("SELECT 1 FROM hb_goal_cooperation WHERE goal=%s",(a.goal,)).fetchone()
        original=c.execute("SELECT to_jsonb(g) AS row FROM hb_goal g WHERE id=%s",(a.goal,)).fetchone()
        original_work=c.execute("SELECT to_jsonb(w) AS row FROM hb_work w WHERE goal=%s ORDER BY id",(a.goal,)).fetchall()
        original_attempts=c.execute("SELECT to_jsonb(t) AS row FROM hb_work_attempt t JOIN hb_work w ON w.id=t.work WHERE w.goal=%s ORDER BY to_jsonb(t)",(a.goal,)).fetchall()
        peer=a.peer
        assert str(store.authenticate(token)["authority"])==str(goal["authority"])
        async def journey():
            async with TestServer(application(store),host="127.0.0.1") as server:
                async with ClientSession() as client:
                    async def configure(reviewer,revision=None,cooperation=0):
                        payload={"operationId":"rollback-cooperation:"+str(uuid4()),
                                 "revision":revision or goal["revision"],"cooperationRevision":cooperation,
                                 "policy":"leader_award","reviewerId":reviewer,"executorIds":[peer]}
                        async with client.post(server.make_url("/hollywood/v3/goals/"+a.goal+"/cooperation"),
                                               json=payload,headers={"Authorization":"Bearer "+token}) as r:
                            return r.status,await r.json()
                    status,result=await configure(str(goal["owner"]))
                    assert status==409,(status,result)
                    if a.mode=="baseline":
                        assert result=={"error":"Identity, revision or operation conflict"},result
                        proof["cases"].append("old_valid_authenticated_policy_rejection_hides_planner_verifier_constraint")
                        return
                    assert result["code"]=="COOPERATION_REVIEWER_IS_PLANNER" and "reviewerId=null" in result["next"]
                    proof["cases"].append("planner_as_independent_verifier_rejected_with_supported_next_action")
                    status,result=await configure(peer)
                    assert status==409 and result["code"]=="COOPERATION_REVIEWER_IS_EXECUTOR",(status,result)
                    proof["cases"].append("executor_cannot_be_its_own_independent_verifier")
                    status,result=await configure(None,cooperation=1)
                    assert status==409 and result["code"]=="COOPERATION_REVISION_CHANGED",(status,result)
                    proof["cases"].append("wrong_cooperation_revision_returns_current_read_guidance")
                    status,result=await configure(None)
                    assert status==200,(status,result)
                    policy=c.execute("SELECT * FROM hb_goal_cooperation WHERE goal=%s",(a.goal,)).fetchone()
                    assert policy["reviewer"] is None and policy["planner"]==goal["owner"] and policy["revision"]==1
                    from hollywood_backbone.cooperation import review,view
                    from hollywood_backbone.store import Denied
                    review(store,c,store.actor(c,str(goal["owner"])),goal)
                    try:
                        review(store,c,store.actor(c,peer),goal)
                    except Denied:pass
                    else:raise AssertionError("Executor gained review authority")
                    assert view(c,a.goal)["reviewAuthority"]=="planner"
                    proof["cases"].append("normal_null_reviewer_retains_planner_review_without_executor_self_acceptance")
                    assert c.execute("SELECT to_jsonb(w) AS row FROM hb_work w WHERE goal=%s ORDER BY id",(a.goal,)).fetchall()==original_work
                    assert c.execute("SELECT to_jsonb(t) AS row FROM hb_work_attempt t JOIN hb_work w ON w.id=t.work WHERE w.goal=%s ORDER BY to_jsonb(t)",(a.goal,)).fetchall()==original_attempts
                    proof["cases"].append("policy_configuration_does_not_admit_work_or_start_agents")
        asyncio.run(journey());c.rollback()
        assert c.execute("SELECT to_jsonb(g) AS row FROM hb_goal g WHERE id=%s",(a.goal,)).fetchone()==original
        assert not c.execute("SELECT 1 FROM hb_goal_cooperation WHERE goal=%s",(a.goal,)).fetchone()
        c.rollback()
    proof.update({"status":"passed","rollbackVerified":True,"productionChanged":False})
    a.output.write_text(json.dumps(proof,indent=2)+"\n");print(json.dumps(proof))
if __name__=="__main__":
    p=argparse.ArgumentParser()
    for n in ("database","bootstrap","output"):p.add_argument("--"+n,type=Path,required=True)
    for n in ("goal","peer"):p.add_argument("--"+n,required=True)
    p.add_argument("--mode",choices=("baseline","candidate"),required=True)
    main(p.parse_args())
