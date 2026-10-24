"""Real captured cancellation/planner failure; rollback-only PG substrate qualification.

This is not a semantic/model result. Run against the explicitly disposable R323
authority before the repaired private API is deployed. Live original rows restore.
"""
import copy
import json
import os
from pathlib import Path
import time

from hollywood_backbone.store import Store, Denied
from hollywood_backbone.context_cancellations import settle_idle
from hollywood_backbone.context_goal_review_frontier import snapshot, changed
from hollywood_backbone.context_goal_dependencies import refresh
from hollywood_backbone.cooperation import review

AUTHORITY="8960d62b-d010-4169-a534-8e3ce6e2cb4a"
GOAL="8cd4705a-296b-5eb2-978b-cc1bb1159e91"
WORK="54848893-83bc-5bfe-be07-3dd5ef046a23"
PARENT="43f9549e-d392-5285-a4b3-21c765c73fb3"
OWNER="57696213-e44a-4527-a5f8-891b8b308a29"
CHECKER="1d725607-2ec4-58e0-a910-01b10e892b57"
REVIEWER="81f12324-8cc4-566a-acd0-55717e51593f"

def one(c,t,key):return c.execute("SELECT * FROM "+t+" WHERE id=%s",(key,)).fetchone()

def main():
    os.umask(0o077)
    out=Path(os.environ["GOAL_CANCEL_EVIDENCE"])
    assert not out.exists();out.mkdir(mode=0o700)
    cfg=json.loads(Path(os.environ["GOAL_CANCEL_DATABASE"]).read_text())
    store=Store(cfg);cases=[]
    def captured(c):
        return {t:c.execute(sql,(AUTHORITY,)).fetchall() for t,sql in {
            "goals":"SELECT * FROM hb_goal WHERE authority=%s ORDER BY id",
            "work":"SELECT * FROM hb_work WHERE authority=%s ORDER BY id",
            "deliveries":"SELECT * FROM hb_delivery WHERE authority=%s ORDER BY id",
            "material":"SELECT m.* FROM hb_context_material m JOIN hb_principal a ON a.id=m.agent WHERE a.authority=%s ORDER BY m.id",
            "permits":"SELECT p.* FROM hb_context_permit p JOIN hb_principal a ON a.id=p.agent WHERE a.authority=%s ORDER BY p.id",
        }.items()}
    try:
        with store.pool.connection() as c:
            before=captured(c);goal=one(c,"hb_goal",GOAL);work=one(c,"hb_work",WORK)
            assert str(goal["authority"])==AUTHORITY and work["state"]=="cancel_requested"
            delivery=one(c,"hb_delivery",work["delivery"])
            material=c.execute("SELECT * FROM hb_context_material WHERE agent=%s AND kind='work' AND source=%s",(CHECKER,delivery["event"])).fetchone()
            assert material["state"]==delivery["state"]=="cancelled"
            permit=c.execute("""SELECT * FROM hb_context_permit WHERE agent=%s AND purpose='writer'
                AND %s=ANY(material_ids) AND native_turn=%s AND state='released'
                AND terminal_status='interrupted'""",(CHECKER,material["id"],delivery["native_turn"])).fetchone()
            assert permit
            assert not c.execute("""SELECT 1 FROM hb_purpose_context b JOIN hb_principal p ON p.id=b.agent
                WHERE p.authority=%s AND b.active_turn IS NOT NULL""",(AUTHORITY,)).fetchone()
            c.rollback()
        def case(name,run):
            with store.pool.connection() as c:
                try:
                    run(c,store.actor(c,OWNER),store.actor(c,CHECKER))
                    cases.append(name)
                finally:c.rollback()
        def reconciliation(c,owner,checker):
            old=one(c,"hb_context_material",PARENT)
            frontier=snapshot(c,store,owner,one(c,"hb_goal",GOAL))["reviewFrontier"]
            assert frontier["canManage"] and frontier["definitionPending"]
            assert frontier["waitingForCancellation"] and not frontier["actionable"],frontier
            refresh(c,store,owner);assert one(c,"hb_context_material",PARENT)==old
            assert settle_idle(c,store,checker)==1
            assert one(c,"hb_work",WORK)["state"]=="cancelled"
            assert one(c,"hb_work",WORK)["revision"]==work["revision"]+1
            assert one(c,"hb_goal",GOAL)["revision"]==goal["revision"]+1
            assert one(c,"hb_delivery",delivery["id"])==delivery
            assert one(c,"hb_context_material",material["id"])==material
            current=snapshot(c,store,owner,one(c,"hb_goal",GOAL))
            assert current["reviewFrontier"]["actionable"]
            assert changed([{"goalId":GOAL,"reviewFrontier":frontier}],[current])
            refresh(c,store,owner);after=one(c,"hb_context_material",PARENT)
            assert after["state"]=="pending" and after["value"]["resumeRevision"]==old["value"].get("resumeRevision",0)+1
            assert settle_idle(c,store,checker)==0
            refresh(c,store,owner);assert one(c,"hb_context_material",PARENT)==after
            assert one(c,"hb_goal",GOAL)["revision"]==goal["revision"]+1
        case("actual_native_terminal_reconciles_and_original_responsibility_resumes_once",reconciliation)
        def hold(c,owner,checker,sql,args):
            c.execute(sql,args)
            assert settle_idle(c,store,checker)==0
            assert one(c,"hb_work",WORK)["state"]=="cancel_requested"
        case("unknown_delivery_holds",lambda c,o,a:hold(c,o,a,"UPDATE hb_delivery SET state='unknown' WHERE id=%s",(delivery["id"],)))
        case("running_permit_holds",lambda c,o,a:hold(c,o,a,"UPDATE hb_context_permit SET state='running' WHERE id=%s",(permit["id"],)))
        case("missing_terminal_receipt_holds",lambda c,o,a:hold(c,o,a,"UPDATE hb_context_permit SET terminal_status=NULL WHERE id=%s",(permit["id"],)))
        def active(c,owner,checker):
            c.execute("UPDATE hb_purpose_context SET active_turn=%s,active_material=%s WHERE agent=%s AND purpose='writer'",
                      (delivery["native_turn"],[material["id"]],CHECKER))
            assert settle_idle(c,store,checker)==0
        case("active_original_native_binding_holds",active)
        def permissions(c,owner,checker):
            planner={**owner,"administrator":False}
            f=snapshot(c,store,planner,goal)["reviewFrontier"]
            assert f["canManage"] and not f["canReview"]
            try:review(store,c,planner,goal)
            except Denied:pass
            else:raise AssertionError("Planner gained independent review permission")
            reviewer=store.actor(c,REVIEWER)
            f=snapshot(c,store,reviewer,goal)["reviewFrontier"]
            assert f["canReview"] and not f["canManage"]
            review(store,c,reviewer,goal)
            assert "reviewFrontier" not in snapshot(c,store,checker,goal)
        case("planner_verifier_and_observer_authority_preserved",permissions)
        def submitted(c,owner,checker):
            # A non-admin planner waits for actual independent review.
            c.execute("UPDATE hb_goal SET definition_revision=adopted_definition_revision WHERE id=%s",(GOAL,))
            c.execute("UPDATE hb_work SET state='submitted' WHERE goal=%s",(GOAL,))
            c.execute("UPDATE hb_delivery SET state='completed' WHERE id IN (SELECT delivery FROM hb_work WHERE goal=%s)",(GOAL,))
            planner={**owner,"administrator":False}
            assert not snapshot(c,store,planner,one(c,"hb_goal",GOAL))["reviewFrontier"]["actionable"]
            assert snapshot(c,store,store.actor(c,REVIEWER),one(c,"hb_goal",GOAL))["reviewFrontier"]["actionable"]
        case("independent_submitted_review_is_not_planner_work",submitted)
        with store.pool.connection() as c:
            assert captured(c)==before
            c.rollback()
        result={"at":time.time(),"state":"passed","cases":cases,"count":len(cases),
                "capturedRowsRestored":True,"modelsStarted":0,"newConversationEvents":0,
                "scope":"Actual captured real PG/native terminal facts plus disclosed rollback-only machine counterfactuals; not model behavior or whole system proof."}
        (out/"result.json").write_text(json.dumps(result,indent=2)+"\n");print(json.dumps(result))
    except BaseException as e:
        (out/"failure.json").write_text(json.dumps({"at":time.time(),"error":repr(e),"completed":cases},indent=2)+"\n")
        raise
    finally:store.close()

if __name__=="__main__":main()
