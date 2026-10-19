"""Real PostgreSQL causal peer continuation, rollback-only and no models."""
import json,time
from pathlib import Path
from psycopg.types.json import Jsonb
from hollywood_backbone.store import Store,Denied,Conflict
from hollywood_backbone import context_peer_updates,context_work_updates
base=Path("/var/lib/lax-three-context-20261018-r01")
store=Store(json.loads((base/"database.json").read_text()))
agent="f2363d21-e7da-4dc5-8749-6736d01d4f68"
input_id="f93c924d-d2b6-4b04-b948-d756b9a5c2e3"
work_id="61622471-7c21-5ce7-960c-32543f5927ed"
try:
 with store.pool.connection() as c:
    actor=store.actor(c,agent)
    before=c.execute("SELECT id,state,revision FROM hb_context_material WHERE agent=%s ORDER BY id",(agent,)).fetchall()
    causal=context_peer_updates.relation(c,actor["id"],input_id)
    assert causal["parentWorkId"]==work_id and causal["responseRequired"]
    context_peer_updates.refresh(c,store,actor)
    source=c.execute("SELECT * FROM hb_context_material WHERE id=%s",(input_id,)).fetchone()
    work=c.execute("SELECT * FROM hb_context_material WHERE id=%s",(work_id,)).fetchone()
    assert source["state"]=="pending" and source["value"]["unadmittedWorkId"]
    reader=c.execute("SELECT * FROM hb_purpose_context WHERE agent=%s AND purpose='reader'",(agent,)).fetchone()
    turn="rollback-causal-reader-r22"
    reader["reads"]={input_id:{"revision":source["revision"],"turn":turn},
                      work_id:{"revision":work["revision"],"turn":turn}}
    context_work_updates.apply(c,store,actor,reader,{"nativeTurn":turn,"value":{
        "inputId":input_id,"workId":work_id,"workRevision":work["revision"],
        "body":"Verified scoped peer continuation; preserve original acceptance and supply prerequisite response."}})
    child=c.execute("SELECT * FROM hb_context_material WHERE id=%s",(source["value"]["unadmittedWorkId"],)).fetchone()
    assert child["state"]=="superseded" and child["value"]["supersededByWorkId"]==work_id
    new_work=c.execute("SELECT * FROM hb_context_material WHERE id=%s",(work_id,)).fetchone()
    assert new_work["value"]["objective"]==work["value"]["objective"]
    assert new_work["value"]["acceptance"]==work["value"]["acceptance"]
    assert new_work["state"]=="pending"
    writer=c.execute("SELECT * FROM hb_purpose_context WHERE agent=%s AND purpose='writer'",(agent,)).fetchone()
    writer["reads"]={work_id:{"revision":new_work["revision"],"turn":"rollback-causal-writer-r22"}}
    result=context_peer_updates.response_material(c,store,actor,writer,{"nativeTurn":"rollback-causal-writer-r22",
        "value":{"workId":work_id,"inputId":input_id,"body":"Verified prerequisite facts, without claiming overall completion."}})
    assert result["state"]=="ready" and result["workCompleted"] is False
    c.rollback()
    assert c.execute("SELECT id,state,revision FROM hb_context_material WHERE agent=%s ORDER BY id",(agent,)).fetchall()==before
 proof={"schema":"lax-causal-peer-continuation-pg/v1","at":time.time(),"modelsStarted":0,
    "originalCrossRoomDelegationMatched":True,"sameInputReclassifiedWithoutRepost":True,
    "unadmittedMisclassificationRetainedAsSuperseded":True,"originalObjectiveAndAcceptancePreserved":True,
    "intermediateRequiredResponseReadyWithoutParentCompletion":True,"allChangesRolledBack":True}
 Path("/aosp/development/lax-three-context-20261018-r01/qualification/causal-peer-updates-pg-r22.json").write_text(json.dumps(proof,indent=2)+"\n")
 print(json.dumps(proof))
finally:store.close()
