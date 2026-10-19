"""Actual PostgreSQL host receipt/dedup gate. All changes roll back; no models."""
import argparse,json,time
from pathlib import Path
from hollywood_backbone.store import Store,Conflict,Denied,digest
from hollywood_backbone.native_effects import transport

class Borrowed:
 def __init__(self,c):self.c=c
 def __enter__(self):self.tx=self.c.transaction();self.tx.__enter__();return self.c
 def __exit__(self,*args):return self.tx.__exit__(*args)
class Pool:
 def __init__(self,c):self.c=c
 def connection(self):return Borrowed(self.c)

def main(args):
 s=Store(json.loads(args.database_config.read_text()))
 config=json.loads(args.controller_config.read_text())
 proof={"schema":"lax-runtime-observation-pg/v1","modelsStarted":0,
        "mode":"Real PostgreSQL rollback-only existing idle work and authenticated controller"}
 actual=s.pool
 try:
  with actual.connection() as c:
   s.pool=Pool(c)
   controller=c.execute("SELECT * FROM hb_principal WHERE id=(SELECT controller FROM hb_host_admission WHERE host=%s)",(config["hostId"],)).fetchone()
   work=c.execute("SELECT * FROM hb_context_material WHERE id=%s",(args.work_id,)).fetchone()
   d=c.execute("SELECT * FROM hb_delivery WHERE event=%s AND agent=%s",(work["source"],work["agent"])).fetchone()
   agent=str(work["agent"]);host=config["hostId"]
   before_events=c.execute("SELECT count(*) AS n FROM hb_event").fetchone()["n"]
   payload={"operationId":"rollback-runtime-observation-r21","machineIdentity":config["machineIdentity"],
       "installationDigest":"d"*64,"laxVersion":"0.1.0.dev76","hollywoodVersion":"0.3.0.dev50",
       "runtimeProtocol":"lax-purpose-runtime/v3","availableCapabilities":["native-peer-provisioning/v1"],
       "workId":args.work_id,"workRevision":work["revision"],"priorNativeTurn":d["native_turn"]}
   def call(actor,token,value):
    with transport(str(actor),digest(token),"POST","runtime_observe","hosts/"+host+"/agents/"+agent+"/runtime-observations",value,None):
     return s.runtime_observe(str(actor),host,agent,value)
   # A real agent credential cannot become the controller, even for Global.
   primary=json.loads(args.installation_intent.read_text())["spec"]["credential"]
   try:call(agent,primary,payload)
   except Denied:proof["agentCannotAssertHostRuntime"]=True
   else:raise AssertionError("Agent acted as host controller")
   result=call(controller["id"],config["token"],payload)
   assert result["workRevision"]==work["revision"]+1
   assert call(controller["id"],config["token"],payload)==result
   current=c.execute("SELECT * FROM hb_context_material WHERE id=%s",(args.work_id,)).fetchone()
   assert current["value"]["objective"]==work["value"]["objective"]
   assert current["state"]=="pending" and current["value"]["resumeRevision"]==work["value"].get("resumeRevision",0)+1
   assert current["value"]["runtimeUpdates"][-1]["authorKind"]=="service"
   try:call(controller["id"],config["token"],{**payload,"installationDigest":"e"*64})
   except Conflict:proof["changedOriginalObservationDenied"]=True
   else:raise AssertionError("Changed original operation accepted")
   assert c.execute("SELECT count(*) AS n FROM hb_event").fetchone()["n"]==before_events
   c.rollback()
   assert c.execute("SELECT revision FROM hb_context_material WHERE id=%s",(args.work_id,)).fetchone()["revision"]==work["revision"]
   proof.update(originalOperationDeduplicated=True,originalObjectivePreserved=True,
       sameWorkBecameEligible=True,attributedAsServiceMetadata=True,messagesCreated=0,
       allChangesRolledBack=True,at=time.time())
  args.evidence_output.write_text(json.dumps(proof,indent=2)+"\n");print(json.dumps(proof))
 finally:s.pool=actual;s.close()
if __name__=="__main__":
 p=argparse.ArgumentParser()
 for name in ("database-config","controller-config","installation-intent","evidence-output"):p.add_argument("--"+name,type=Path,required=True)
 p.add_argument("--work-id",required=True)
 main(p.parse_args())
