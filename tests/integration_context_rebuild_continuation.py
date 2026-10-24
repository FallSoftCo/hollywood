"""Actual PostgreSQL rollback check for request-bound rebuild continuation.

Synthetic admission/material/native checkpoint metadata; no model/native execution,
user message submission, canonical Work completion, or production rollout.
"""
import json,os,time
from pathlib import Path
from uuid import uuid4
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from hollywood_backbone.store import Store,Conflict
from hollywood_backbone import context_selection,context_capacity,context_system

A="5bbf73ca-5884-43f1-9c4b-fcd67b56a8f9"
SOURCE="6428ec70-8364-40b4-b0a7-7cf2e6b8c578"

def main():
 evidence=Path(os.environ["REBUILD_EVIDENCE"]);assert not evidence.exists()
 evidence.mkdir(mode=0o700)
 cfg=json.loads(Path(os.environ["REBUILD_DATABASE"]).read_text())
 store=Store(cfg);checks=[]
 try:
  with psycopg.connect(**cfg,row_factory=dict_row,autocommit=True) as c:
   baseline=c.execute("SELECT count(*) AS n FROM hb_context_material").fetchone()["n"]
   with c.transaction(force_rollback=True):
    actor=c.execute("SELECT * FROM hb_principal WHERE id=%s",(A,)).fetchone()
    binding=c.execute("SELECT * FROM hb_purpose_context WHERE agent=%s AND purpose='writer' FOR UPDATE",(A,)).fetchone()
    assert binding["active_turn"] is None and not binding["rebuild_requested"]
    ids=[str(uuid4()),str(uuid4())];thread=binding["native_thread"];turn="disclosed-rebuild-domain-fixture"
    original={"objective":"Keep this original bounded deliverable","acceptance":"Original exact artifact requirement",
      "execution":{"status":"completed","nativeThread":thread,"nativeTurn":turn,"resumeRevision":0}}
    for ident in ids:
     c.execute("INSERT INTO hb_context_material(id,agent,kind,source,state,value) VALUES(%s,%s,'work',%s,'pending',%s)",
       (ident,A,SOURCE,Jsonb(original)))
    c.execute("UPDATE hb_purpose_context SET active_turn=%s,accepting=true,active_material=%s::uuid[] WHERE agent=%s AND purpose='writer'",(turn,[ids[0]],A))
    binding=c.execute("SELECT * FROM hb_purpose_context WHERE agent=%s AND purpose='writer'",(A,)).fetchone()
    p={"operationId":"disclosed-request-fixture","nativeThread":thread,"nativeTurn":turn,"value":{}}
    result=context_selection.request(c,actor,"writer",binding,p)
    a,b=[c.execute("SELECT * FROM hb_context_material WHERE id=%s",(x,)).fetchone() for x in ids]
    assert a["value"].get("resumeRevision",0)==1,"Requesting unfinished Work was not released for continuation"
    assert b["value"].get("resumeRevision",0)==0,"Unrelated consumed Work woke"
    assert a["source"]==b["source"] and all(a["value"][k]==original[k] for k in ("objective","acceptance"))
    checks.append("Only requesting original unfinished Work receives continuation")
    current=c.execute("SELECT * FROM hb_purpose_context WHERE agent=%s AND purpose='writer'",(A,)).fetchone()
    try:context_selection.request(c,actor,"writer",current,{**p,"operationId":"cannot-overwrite-original-request"})
    except Conflict:pass
    else:raise AssertionError("Another request replaced the original unresolved rebuild")
    checks.append("A new request cannot overwrite an unresolved original rebuild")
    c.execute("UPDATE hb_purpose_context SET active_turn=NULL,accepting=false,active_material='{}' WHERE agent=%s AND purpose='writer'",(A,))
    binding=c.execute("SELECT * FROM hb_purpose_context WHERE agent=%s AND purpose='writer'",(A,)).fetchone()
    try:context_capacity.reserve(store,c,actor,"writer",binding,{"operationId":"must-not-admit","nativeThread":thread,"value":{"materialIds":[ids[0]]}})
    except Conflict as e:assert "rebuild" in str(e).lower()
    else:raise AssertionError("Inference admitted before original rebuild installed")
    checks.append("Uninstalled original rebuild fences new inference admission")
    # Disclosed synthetic installed receipt records domain provenance only.
    # Its snapshot was produced by the real implementation in this transaction.
    p2={"operationId":"disclosed-snapshot-fixture","nativeThread":thread,"nativeTurn":"context-maintenance","value":{"materialIds":[ids[0]]}}
    snapshot=context_selection.snapshot(c,store,actor,"writer",binding,p2)
    c.execute("INSERT INTO hb_context_operation VALUES(%s,'writer',%s,%s,%s)",(A,p2["operationId"],"disclosed-test-only",Jsonb(snapshot)))
    installed=context_selection.installed(c,store,actor,"writer",binding,{"operationId":"disclosed-installed-fixture","nativeThread":thread,"nativeTurn":"context-maintenance","value":{
      "snapshotOperationId":p2["operationId"],"sha256":snapshot["sha256"],"checkpointTurnId":"synthetic-not-native",
      "nativeHistorySha256":"a"*64,"nativeHistoryBytes":1,"nativeActionsSha256":"b"*64}})
    after=c.execute("SELECT * FROM hb_purpose_context WHERE agent=%s AND purpose='writer'",(A,)).fetchone()
    assert after["rebuild_requested"] is None and installed["generation"]==binding["rebuild_generation"]+1
    # Use actual pending-query semantics, restricted to this disclosed pair.
    pending=c.execute("""SELECT id FROM hb_context_material WHERE id=ANY(%s::uuid[]) AND state IN ('pending','running')
     AND coalesce((value->>'resumeRevision')::bigint,0)>coalesce((value->'execution'->>'resumeRevision')::bigint,-1)""",(ids,)).fetchall()
    assert [str(x["id"]) for x in pending]==[ids[0]]
    checks.append("Installed rebuild leaves only requesting Work eligible")
    # An idle maintenance request has no captured execution and must wake none.
    before={x:c.execute("SELECT value,revision FROM hb_context_material WHERE id=%s",(x,)).fetchone() for x in ids}
    context_selection.request(c,actor,"writer",after,{"operationId":"idle-maintenance-fixture","nativeThread":thread,"nativeTurn":"context-maintenance","value":{}})
    assert before=={x:c.execute("SELECT value,revision FROM hb_context_material WHERE id=%s",(x,)).fetchone() for x in ids}
    checks.append("Idle maintenance never wakes consumed original Work")
    for ident,terminal in zip(ids,("complete","cancelled")):
     c.execute("UPDATE hb_context_material SET state=%s WHERE id=%s",(terminal,ident))
     previous=c.execute("SELECT value,revision FROM hb_context_material WHERE id=%s",(ident,)).fetchone()
     c.execute("UPDATE hb_purpose_context SET rebuild_requested=NULL,active_turn=%s,accepting=true,active_material=%s::uuid[] WHERE agent=%s AND purpose='writer'",(turn,[ident],A))
     current=c.execute("SELECT * FROM hb_purpose_context WHERE agent=%s AND purpose='writer'",(A,)).fetchone()
     outcome=context_selection.request(c,actor,"writer",current,{**p,"operationId":"terminal-fixture-"+terminal})
     assert outcome["continuationMaterialIds"]==[]
     assert previous==c.execute("SELECT value,revision FROM hb_context_material WHERE id=%s",(ident,)).fetchone()
    checks.append("Completed and cancelled Work never receives rebuild continuation")
   assert c.execute("SELECT count(*) AS n FROM hb_context_material").fetchone()["n"]==baseline
  result={"at":time.time(),"state":"mechanical_domain_passed","checks":checks,"rollbackVerified":True,
    "modelsStarted":0,"newHumanPosts":0,"nativeCheckpointProof":False,"productionReplacementReady":False}
  (evidence/"result.json").write_text(json.dumps(result,indent=2)+"\n")
  print(json.dumps({"state":result["state"],"checks":len(checks),"modelsStarted":0}))
 finally:store.close()

if __name__=="__main__":main()
