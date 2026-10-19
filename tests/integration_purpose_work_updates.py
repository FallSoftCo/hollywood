"""Real PostgreSQL reader/answer continuity; all new input/turn facts roll back."""
import argparse,json,time
from pathlib import Path
from uuid import uuid4
from psycopg.types.json import Jsonb
from hollywood_backbone.store import Store,Conflict,Denied,digest
from hollywood_backbone.context_system import ingest,perform
from hollywood_backbone.context_material import read
from hollywood_backbone.human_continuations import context
from fixture_context_capacity import lifecycle

def main(args):
 s=Store(json.loads(args.database_config.read_text()))
 proof={"schema":"lax-purpose-work-updates-pg/v1","modelsStarted":0,
        "mode":"Real PostgreSQL rollback-only input and reader-turn fixture"}
 try:
  with s.pool.connection() as c:
   a=s.actor(c,args.agent_id)
   owner=c.execute("SELECT * FROM hb_principal WHERE authority=%s AND kind='human' AND administrator AND active LIMIT 1",(a["authority"],)).fetchone()
   w=c.execute("SELECT * FROM hb_context_material WHERE id=%s AND agent=%s",(args.work_id,a["id"])).fetchone()
   d=c.execute("SELECT * FROM hb_delivery WHERE event=%s AND agent=%s",(w["source"],a["id"])).fetchone()
   before=c.execute("SELECT count(*) AS n FROM hb_event").fetchone()["n"]
   objective=w["value"]["objective"]
   original_revision=w["revision"]
   c.execute("UPDATE hb_context_material SET state='pending' WHERE id=%s",(w["id"],))
   question=str(uuid4())
   event=s.event(c,a,str(c.execute("SELECT room FROM hb_event WHERE id=%s",(d["event"],)).fetchone()["room"]),
       "question","Rollback-only question",audience="direct",recipient=owner["id"],source=d["event"])
   answer=s.event(c,owner,str(c.execute("SELECT room FROM hb_event WHERE id=%s",(d["event"],)).fetchone()["room"]),
       "request","Rollback-only authenticated answer",audience="direct",recipient=a["id"],source=event,
       data={"type":"humanQuestionAnswer","attentionId":question,"originalDeliveryId":str(d["id"])})
   answer_delivery=str(uuid4())
   c.execute("INSERT INTO hb_delivery(id,authority,event,agent) VALUES(%s,%s,%s,%s)",
             (answer_delivery,a["authority"],answer,a["id"]))
   q={"id":question,"delivery":d["id"],"generation":d["generation"],"agent":a["id"],
      "native_thread":d["native_thread"],"native_turn":d["native_turn"]}
   # The original writer binding is genuine recorded history. This new question/input is a fixture.
   binding=context(c,q)
   assert binding["bindingKind"]=="purpose"
   c.execute("""INSERT INTO hb_attention(id,authority,delivery,agent,human,room,event,generation,claim_id,
      native_thread,native_turn,native_slot,method,request,digest,state,response_event)
      SELECT %s,authority,id,agent,%s,(SELECT room FROM hb_event WHERE id=hb_delivery.event),%s,
        generation,claim_id,native_thread,native_turn,%s,'hollywood/question',%s,%s,'response_submitted',%s
      FROM hb_delivery WHERE id=%s""",
      (question,owner["id"],event,"rollback-"+question,Jsonb({"isBlocking":False,"questions":[]}),"f"*64,answer,d["id"]))
   ingest(c,a["id"],answer)
   assert not c.execute("SELECT 1 FROM hb_context_material WHERE source=%s AND kind='need'",(answer,)).fetchone()
   def reader():
    return c.execute("SELECT * FROM hb_purpose_context WHERE agent=%s AND purpose='reader'",(a["id"],)).fetchone()
   b=reader();turn="rollback-reader-update-"+str(uuid4())
   p={"operationId":"reader-update:"+turn,"action":"begin","nativeThread":b["native_thread"],
      "nativeTurn":turn,"value":{"materialIds":[str(answer)]}}
   lifecycle(c,s,a,"reader",b,p)
   command={**p,"action":"amend","value":{"inputId":str(answer),"workId":args.work_id,
      "workRevision":original_revision,"body":"Human supplied an additional acceptance detail; retain the original objective."}}
   try:perform(s,c,a,"reader",reader(),command)
   except Conflict:proof["unreadMaterialDenied"]=True
   else:raise AssertionError("Unread update accepted")
   for identity in (str(answer),args.work_id):
    offset=0;revision=None
    while True:
     page=read(c,s,a,"reader",reader(),turn,{"reference":identity,"offset":offset,**({"revision":revision} if revision else {})})
     revision=page["revision"];offset=page["nextOffset"]
     if offset is None:break
   result=perform(s,c,a,"reader",reader(),command)
   after=c.execute("SELECT * FROM hb_context_material WHERE id=%s",(w["id"],)).fetchone()
   assert after["value"]["objective"]==objective and after["state"]!="complete"
   assert after["value"]["resumeRevision"]==w["value"].get("resumeRevision",0)+1
   assert after["value"]["updates"][-1]["sourceEventId"]==str(answer)
   assert c.execute("SELECT state FROM hb_attention WHERE id=%s",(question,)).fetchone()["state"]=="closed"
   assert c.execute("SELECT state FROM hb_delivery WHERE id=%s",(answer_delivery,)).fetchone()["state"]=="completed"
   assert not c.execute("SELECT 1 FROM hb_context_material WHERE source=%s AND kind='work'",(answer,)).fetchone()
   proof.update(originalWriterContextVerified=True,originalObjectivePreserved=True,
       answerAmendedSameWork=True,readerRequiredCompleteMaterial=True,
       noDuplicateWork=True,noAcknowledgementObligation=True,
       questionClosedOnReaderConsumption=True,writerSteered=False)
   c.rollback()
   assert c.execute("SELECT count(*) AS n FROM hb_event").fetchone()["n"]==before
   proof.update(allSyntheticRowsRolledBack=True,at=time.time())
  args.evidence_output.write_text(json.dumps(proof,indent=2)+"\n");print(json.dumps(proof))
 finally:s.close()
if __name__=="__main__":
 p=argparse.ArgumentParser()
 p.add_argument("--database-config",type=Path,required=True);p.add_argument("--agent-id",required=True)
 p.add_argument("--work-id",required=True);p.add_argument("--evidence-output",type=Path,required=True)
 main(p.parse_args())
