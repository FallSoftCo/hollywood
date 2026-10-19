"""Rollback-only real PostgreSQL authority check. Liveness is a frozen fixture,
not evidence that an installed process or real model has been qualified."""
import argparse,json,hashlib,time
from pathlib import Path
from uuid import uuid4
from psycopg.types.json import Jsonb
from hollywood_backbone.store import Store,Conflict,Denied,digest
from hollywood_backbone import native_effects
from fixture_context_capacity import lifecycle

def main(args):
    store=Store(json.loads(args.database_config.read_text()))
    config=json.loads(args.agent_config.read_text())
    proof={"schema":"lax-purpose-effects-pg/v1","modelsStarted":0,
           "mode":"Real PostgreSQL transaction; rollback-only frozen process/turn facts"}
    try:
      with store.pool.connection() as c:
        a=store.actor(c,config["agentId"])
        def counts():
            return {t:c.execute("SELECT count(*) AS n FROM "+t).fetchone()["n"]
                for t in ("hb_event","hb_delivery","hb_principal","hb_executor","hb_native_effect","hb_room")}
        before=counts()
        w=c.execute("SELECT * FROM hb_context_material WHERE id=%s AND agent=%s",(args.work_id,a["id"])).fetchone()
        d=c.execute("SELECT * FROM hb_delivery WHERE event=%s AND agent=%s",(w["source"],a["id"])).fetchone()
        c.execute("INSERT INTO hb_claim VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING",(a["id"],d["claim_id"],d["host"],d["id"]))
        c.execute("UPDATE hb_delivery SET state='claimed',cancellation_requested=false WHERE id=%s",(d["id"],))
        c.execute("UPDATE hb_context_material SET state='pending' WHERE id=%s",(args.work_id,))
        b=c.execute("SELECT * FROM hb_purpose_context WHERE agent=%s AND purpose='writer'",(a["id"],)).fetchone()
        turn="rollback-effect-"+str(uuid4())
        p={"action":"begin","operationId":"context-begin:"+turn,"nativeThread":b["native_thread"],
           "nativeTurn":turn,"value":{"materialIds":[args.work_id]}}
        accepted=lifecycle(c,store,a,"writer",b,p)
        c.execute("INSERT INTO hb_context_operation VALUES(%s,'writer',%s,%s,%s)",
            (a["id"],p["operationId"],digest(p),Jsonb(accepted)))
        scope=accepted["executionScopes"][0]
        source={"schema":"hollywood-native-source/v3",**{k:scope[k] for k in (
            "authorityId","agentId","hostId","deliveryId","claimId","generation","nativeThread",
            "nativeTurn","contextId","roleRevision","executorId")},
            "requestId":scope["sourceRequestId"],"executionScopeId":scope["id"],
            "nativeGoalId":None,"callId":"rollback-business","tool":"hollywood_create_room",
            "argumentsDigest":"a"*64}
        source=native_effects.source(json.dumps(source))
        def validate(value,token):
            with native_effects.transport(config["agentId"],digest(token),"POST","create_room","rooms",{},value):
                return store.actor(c,config["agentId"])
        current=validate(source,config["tokens"]["writer"])
        identity=str(uuid4())
        created=store._admin_create_room(c,current,identity,"Disposable transaction proof",
             "Rollback-only purpose-native business effect",config["agentId"])
        assert c.execute("SELECT 1 FROM hb_room WHERE id=%s",(identity,)).fetchone()
        proof["currentAdmittedWriterCanMutate"]=True
        def denied(value,token):
            try:
                with c.transaction():
                    validate(value,token)
            except (Conflict,Denied):
                return True
            raise AssertionError("Invalid native source admitted")
        proof["readerCredentialCannotBorrowWriterScope"]=denied(source,config["tokens"]["reader"])
        proof["ChangedScopeDenied"]=denied({**source,"executionScopeId":"execution-"+"0"*64},config["tokens"]["writer"])
        c.execute("UPDATE hb_executor SET state='unknown' WHERE id=%s",(source["executorId"],))
        proof["UnknownProcessDenied"]=denied(source,config["tokens"]["writer"])
        c.execute("UPDATE hb_executor SET state='live' WHERE id=%s",(source["executorId"],))
        c.execute("UPDATE hb_purpose_context SET accepting=false WHERE agent=%s AND purpose='writer'",(a["id"],))
        proof["ClosedTurnDenied"]=denied(source,config["tokens"]["writer"])
        c.rollback()
        assert counts()==before
        proof.update(allSyntheticRecordsRolledBack=True,at=time.time())
      args.evidence_output.write_text(json.dumps(proof,indent=2)+"\n")
      print(json.dumps(proof))
    finally:store.close()

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--database-config",type=Path,required=True)
    p.add_argument("--agent-config",type=Path,required=True)
    p.add_argument("--work-id",required=True)
    p.add_argument("--evidence-output",type=Path,required=True)
    main(p.parse_args())
