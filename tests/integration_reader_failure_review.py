"""Actual PG/authenticated HTTP captured Reader-failure boundary; rollback only.

Synthetic native begin/end/permit facts verify protocol, not model behavior.
The actual failed live Reader continuation is a separate qualification.
"""
import argparse,asyncio,contextlib,copy,json,secrets,time
from pathlib import Path
from uuid import uuid4
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg import sql
from aiohttp import ClientSession
from aiohttp.test_utils import TestServer
from hollywood_backbone.server import application
from hollywood_backbone.store import Store,digest

def main(a):
    assert not a.output.exists(),"Inspect existing result"
    m=json.loads(a.installation.read_text())
    setup=json.loads(a.bootstrap.read_text())
    proof={"at":time.time(),"mode":a.mode,"modelsStarted":0,
           "scope":"Actual PG/authenticated HTTP, uncommitted synthetic identity and captured-shape Reader facts. No native process, business effect or model response simulated as real.",
           "cases":[]}
    with psycopg.connect(**json.loads(a.database.read_text()),row_factory=dict_row) as c:
        class Borrow:
            @contextlib.contextmanager
            def connection(self):
                with c.transaction():yield c
        store=Store.__new__(Store);store.pool=Borrow()
        actorid,context,turn,thread,permit=[str(uuid4()) for _ in range(5)]
        token=secrets.token_urlsafe(32);td=digest(token);source=m["agentId"]
        def clone(table,where,params,override):
            query=sql.SQL("INSERT INTO {t} SELECT (jsonb_populate_record(NULL::{t},to_jsonb(s)||%s::jsonb)).* FROM {t} s WHERE ").format(t=sql.Identifier(table))+sql.SQL(where)
            assert c.execute(query,(Jsonb(override),*params)).rowcount==1
        original_principals=c.execute("SELECT count(*) AS n FROM hb_principal").fetchone()["n"]
        clone("hb_principal","id=%s",(source,),{"id":actorid,"name":"rollback-reader:"+actorid,"administrator":False})
        clone("hb_agent_role","principal=%s",(source,),{"principal":actorid})
        clone("hb_placement","agent=%s",(source,),{"agent":actorid,"enabled":True})
        clone("hb_context_system","agent=%s",(source,),{"agent":actorid})
        role=c.execute("SELECT * FROM hb_agent_role WHERE principal=%s",(actorid,)).fetchone()
        c.execute("INSERT INTO hb_membership(authority,room,principal,can_read,can_post,can_request) VALUES(%s,%s,%s,true,true,true)",
                  (m["authorityId"],role["home_room"],actorid))
        b=c.execute("SELECT * FROM hb_purpose_context WHERE agent=%s AND purpose='reader'",(source,)).fetchone()
        clone("hb_token","digest=%s",(b["token_digest"],),{"digest":td,"principal":actorid,"revoked":False})
        clone("hb_purpose_context","agent=%s AND purpose='reader'",(source,),
              {"agent":actorid,"context_id":context,"token_digest":td,"native_thread":thread,
               "active_turn":turn,"active_material":[],"accepting":True,"reads":{}})
        assert str(store.authenticate(token)["id"])==actorid
        owner=store.authenticate(setup["credentials"]["Owner"]["token"])
        public=store.post_in_transaction(c,owner,str(role["home_room"]),"rollback-public:"+str(uuid4()),
              {"kind":"request","audience":"room","recipientId":actorid,"body":"Protocol fixture public responsibility; no execution"})
        private=store.post_in_transaction(c,owner,str(role["home_room"]),"rollback-private:"+str(uuid4()),
              {"kind":"message","audience":"direct","recipientId":actorid,"body":"Private protocol fixture observation"})
        observation,work=str(uuid4()),str(uuid4())
        c.execute("INSERT INTO hb_context_material(id,agent,kind,source,state,value) VALUES(%s,%s,'input',%s,'pending',%s)",
                  (observation,actorid,private["event"]["id"],Jsonb({"roomObservation":True,"responseRequired":False})))
        c.execute("INSERT INTO hb_context_material(id,agent,kind,source,state,value) VALUES(%s,%s,'work',%s,'waiting',%s)",
                  (work,actorid,public["event"]["id"],Jsonb({"objective":"Preserve this original task","acceptance":"No execution","resumeRevision":0})))
        def material(identity):return c.execute("SELECT * FROM hb_context_material WHERE id=%s",(identity,)).fetchone()
        originalwork=material(work)
        reads={observation:{"revision":1,"turn":turn},work:{"revision":1,"turn":turn}}
        c.execute("UPDATE hb_purpose_context SET active_material=%s::uuid[],reads=%s WHERE agent=%s AND purpose='reader'",
                  ([observation],Jsonb(reads),actorid))
        async def run(phase):
            async with TestServer(application(store),host="127.0.0.1") as server:
                async with ClientSession() as client:
                    async def call(action,value,operation=None,turn_id=turn,thread_id=thread):
                        payload={"operationId":operation or "rollback-reader:"+str(uuid4()),
                                 "runtimeProtocol":"lax-purpose-runtime/v3","nativeThread":thread_id,
                                 "nativeTurn":turn_id,"action":action,"value":value}
                        async with client.post(server.make_url("/hollywood/v3/agents/"+actorid+"/contexts/reader"),
                                               json=payload,headers={"Authorization":"Bearer "+token}) as r:
                            return r.status,await r.json()
                    if phase=="privacy":
                        status,result=await call("observe",{"observationId":observation,"body":"Private fact","workId":work,"workRevision":1})
                        assert status==403,(status,result)
                        assert material(work)==originalwork and material(observation)["state"]=="pending"
                        if a.mode=="baseline":
                            assert result=={"error":"Access denied"}
                        else:
                            assert result["code"]=="PRIVATE_OBSERVATION_AUDIENCE" and "without workId" in result["next"]
                            status,result=await call("observe",{"observationId":observation,"body":"Retain privately without attaching to another audience"})
                            assert status==200 and material(observation)["state"]=="handled"
                            assert material(work)==originalwork
                        proof["cases"].append("private_to_room_work_denied_without_leak_or_work_change")
                        if a.mode=="candidate":proof["cases"].append("actionable403_and_private_record_without_work_supported")
                        return
                    value={"nativeTurn":turn,"nativeItemsSha256":"a"*64,"callbacksReconciled":True}
                    status,result=await call("acknowledge_reader_failure",{**value,"callbacksReconciled":False})
                    assert status==(400 if a.mode=="candidate" else 400),(status,result)
                    if a.mode=="baseline":
                        status,result=await call("acknowledge_reader_failure",value)
                        assert status==400 and material(observation)["revision"]==1
                        proof["cases"].append("old_runtime_has_no_explicit_failed_reader_review")
                        return
                    for kwargs in [{"turn_id":str(uuid4())},{"thread_id":str(uuid4())}]:
                        status,result=await call("acknowledge_reader_failure",value,**kwargs)
                        assert status==409,(status,result)
                    c.execute("UPDATE hb_placement SET enabled=false WHERE agent=%s",(actorid,))
                    status,result=await call("acknowledge_reader_failure",value);assert status==403
                    c.execute("UPDATE hb_placement SET enabled=true WHERE agent=%s",(actorid,))
                    proof["cases"].append("wrong_original_thread_turn_or_paused_authority_rejected")
                    op="review-reader-failure:"+turn
                    status,result=await call("acknowledge_reader_failure",value,op)
                    assert status==200,(status,result)
                    assert result["continuedInputIds"]==[observation] and result["failedOriginalRetained"] is True
                    assert material(observation)["state"]=="pending" and material(observation)["revision"]==2
                    assert material(work)==originalwork
                    assert c.execute("SELECT terminal_status FROM hb_context_permit WHERE id=%s",(permit,)).fetchone()["terminal_status"]=="failed"
                    proof["cases"].append("only_original_pending_input_revised_failure_and_work_unchanged_no_inference")
                    status,duplicate=await call("acknowledge_reader_failure",value,op)
                    assert status==200 and duplicate==result and material(observation)["revision"]==2
                    status,another=await call("acknowledge_reader_failure",value)
                    assert status==200 and another["continuedInputIds"]==[] and material(observation)["revision"]==2
                    proof["cases"].append("same_or_different_operation_cannot_bump_original_input_again")
                    status,result=await call("acknowledge_reader_failure",{**value,"nativeItemsSha256":"b"*64})
                    assert status==409 and material(observation)["revision"]==2
                    proof["cases"].append("changed_original_native_evidence_rejected")
        with c.transaction(force_rollback=True):
            asyncio.run(run("privacy"))
        assert material(observation)["state"]=="pending" and material(observation)["revision"]==1
        c.execute("UPDATE hb_purpose_context SET active_turn=NULL,active_material='{}',accepting=false,reads='{}' WHERE agent=%s",(actorid,))
        latest=c.execute("SELECT id FROM hb_context_permit WHERE agent=%s AND purpose='reader' ORDER BY ticket DESC LIMIT 1",(source,)).fetchone()
        clone("hb_context_permit","id=%s",(latest["id"],),
              {"id":permit,"agent":actorid,"purpose":"reader","context_id":context,"native_thread":thread,"native_turn":turn,
               "operation":"rollback-failed:"+str(uuid4()),"material_ids":[observation],"state":"released","terminal_status":"failed"})
        for label,result in [("begin",{"nativeTurn":turn,"state":"running","materialIds":[observation]}),
                             ("end",{"nativeTurn":turn,"state":"failed","admission":{"capacityReleased":True,"permitId":permit}})]:
            c.execute("INSERT INTO hb_context_operation(agent,purpose,operation,digest,result) VALUES(%s,'reader',%s,%s,%s)",
                      (actorid,"context-"+label+":"+turn,digest(result),Jsonb(result)))
        asyncio.run(run("review"));c.rollback()
        assert c.execute("SELECT count(*) AS n FROM hb_principal").fetchone()["n"]==original_principals
        assert not c.execute("SELECT 1 FROM hb_principal WHERE id=%s",(actorid,)).fetchone()
        c.rollback()
    proof.update(status="passed",authenticatedIdentityVerified=True,rollbackVerified=True,productionChanged=False)
    a.output.write_text(json.dumps(proof,indent=2)+"\n");print(json.dumps(proof))
if __name__=="__main__":
    p=argparse.ArgumentParser()
    for n in ["database","installation","bootstrap","output"]:p.add_argument("--"+n,type=Path,required=True)
    p.add_argument("--mode",choices=["baseline","candidate"],required=True)
    main(p.parse_args())
