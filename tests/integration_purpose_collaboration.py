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
    proof={"schema":"lax-purpose-collaboration-pg/v1","modelsStarted":0,
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
        from hollywood_backbone import collaboration, purpose_effects
        controller=json.loads((Path("/var/lib/harness")/config["authorityId"]/"hosts"/config["hostId"]/"controller.json").read_text())
        relay=store.actor(c,controller["principalId"] if "principalId" in controller else str(
            c.execute("SELECT controller FROM hb_host_admission WHERE host=%s",(config["hostId"],)).fetchone()["controller"]))
        c.execute("""INSERT INTO hb_collaboration_bridge(authority,host,controller,state,adapter_sha256,enrollment_sha256,protocol)
            VALUES(%s,%s,%s,'active',%s,%s,'canonical-edit-plans/v2')
            ON CONFLICT(host) DO UPDATE SET state='active',controller=EXCLUDED.controller""",
            (a["authority"],config["hostId"],relay["id"],"a"*64,"b"*64))
        bridge=c.execute("SELECT * FROM hb_collaboration_bridge WHERE host=%s",(config["hostId"],)).fetchone()
        c.execute("""UPDATE hb_executor SET observed_at=clock_timestamp(),evidence=%s WHERE id=%s""",
            (Jsonb({"serviceBindingDigest":"c"*64,"reason":"matched-live-incarnation"}),source["executorId"]))
        executor=c.execute("SELECT * FROM hb_executor WHERE id=%s",(source["executorId"],)).fetchone()
        callback={**source,"tool":"apply_patch"}
        payload={"source":callback,"bridgeRevision":bridge["revision"],
            "executorId":source["executorId"],"descriptorDigest":executor["descriptor_digest"],
            "serviceBindingDigest":"c"*64,"command":{"action":"preflight"}}
        def relay_call(body=payload,identity=relay):
            with native_effects.transport(str(identity["id"]),digest(controller["token"]),"POST",
                    "delivery_edit_call","collaboration/delivery-edits",body,None):
                current_relay=store.actor(c,identity["id"])
                return collaboration.participation(store,c,current_relay,body,
                    tools={"apply_patch"},patch_actions={"preflight","begin","settle"})
        _,accepted_source,real_binding=relay_call()
        assert real_binding["schema"]=="lax-purpose-execution-binding/v1"
        assert real_binding["workspace_id"]==scope["workspaceId"]
        assert accepted_source["executionScopeId"]==scope["id"]
        projection=store.claim_receipt(c,d["id"])
        assert projection["purposeExecutionScope"]==scope
        assert not projection.get("agentContextBinding")
        proof["protectedRelayUsesOriginalPurposeScope"]=True
        proof["noLegacyContextRowFabricated"]=True
        proof["physicalNamespaceSeparateFromContext"]=True
        def relay_denied(body):
            try:
                with c.transaction():relay_call(body)
            except (Conflict,Denied):
                return True
            raise AssertionError("Changed protected collaboration callback admitted")
        proof["relayChangedScopeDenied"]=relay_denied({**payload,"source":{
            **callback,"executionScopeId":"execution-"+"0"*64}})
        proof["relayChangedProcessDigestDenied"]=relay_denied({**payload,"descriptorDigest":"0"*64})
        c.execute("UPDATE hb_executor SET observed_at=clock_timestamp()-interval '1 day' WHERE id=%s",
            (source["executorId"],))
        proof["relayStaleProcessDenied"]=relay_denied(payload)
        c.execute("UPDATE hb_executor SET observed_at=clock_timestamp() WHERE id=%s",(source["executorId"],))

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
