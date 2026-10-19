"""Actual authenticated HTTP/PG contract; disposable fixture writes all rolled back."""
import asyncio,contextlib,json,time
from pathlib import Path
from uuid import uuid4
from aiohttp import ClientSession
from aiohttp.test_utils import TestServer
from psycopg.types.json import Jsonb
from hollywood_backbone.store import Store
from hollywood_backbone.server import application
from hollywood_backbone.context_room_observations import refresh,enroll
base=Path("/var/lib/lax-three-context-20261018-r01")
authority="f96e5615-0e6d-48d3-93b6-dc54393d5c2e"
a="3df5ce3f-334d-5942-b99b-84fe7436bd19"
global_id="f2363d21-e7da-4dc5-8749-6736d01d4f68"
room="c5cf33ac-b91b-53a6-82c8-284e4a9dd605"
cfg=json.loads((Path("/var/lib/harness")/authority/"purpose-agents"/a/"home/agent.json").read_text())
setup=json.loads((base/"setup-intent.json").read_text())
store=Store(json.loads((base/"database.json").read_text()))
store.migrate()  # Additive private schema installation; fixture rows below roll back.
async def run(c):
 actor=store.actor(c,a);g=store.actor(c,global_id)
 human=store.actor(c,setup["credentials"]["Owner"]["principalId"])
 binding=c.execute("SELECT * FROM hb_purpose_context WHERE agent=%s AND purpose='reader'",(a,)).fetchone()
 assert binding["active_turn"] is None
 c.execute("UPDATE hb_membership SET can_post=false,can_request=false WHERE principal=%s AND room=%s",(a,room))
 enroll(c,actor);enroll(c,g)
 initial=c.execute("SELECT seq FROM hb_context_observation_cursor WHERE agent=%s",(a,)).fetchone()["seq"]
 event=store.event(c,g,room,"message","The bounded test backend now exports a read-only feature summary.")
 private=store.event(c,g,room,"message","PRIVATE TO HUMAN ONLY",audience="direct",recipient=human["id"])
 own=store.event(c,actor,room,"message","OWN TEST MESSAGE, NO SELF WAKE")
 work_source=store.event(c,human,room,"request","Existing bounded fixture documentation responsibility.",recipient=actor["id"])
 delivery=str(uuid4());work=str(uuid4())
 c.execute("INSERT INTO hb_delivery(id,authority,event,agent) VALUES(%s,%s,%s,%s)",(delivery,authority,work_source,a))
 c.execute("INSERT INTO hb_context_material(id,agent,kind,source,state,value) VALUES(%s,%s,'work',%s,'pending',%s)",
  (work,a,work_source,Jsonb({"objective":"Retain this exact original objective","acceptance":"A bounded verified artifact","progress":[],"evidence":[]})))
 before=c.execute("SELECT count(*) AS n FROM hb_delivery").fetchone()["n"]
 needs=c.execute("SELECT count(*) AS n FROM hb_context_material WHERE kind='need'").fetchone()["n"]
 class Borrow:
  @contextlib.contextmanager
  def connection(self):
   with c.transaction():yield c
 original_pool=store.pool;store.pool=Borrow()
 server=TestServer(application(store),host="127.0.0.1");await server.start_server()
 proof={"schema":"lax-room-observations-http/v1","at":time.time(),"mode":"Actual HTTP/real PostgreSQL, frozen native binding and disposable semantic fixture; all changes rolled back","modelsStarted":0}
 try:
  async with ClientSession() as client:
   async def call(action,value=None,turn=None,method="POST",path=None):
    body={"runtimeProtocol":"lax-purpose-runtime/v3","operationId":"qa-observation:"+str(uuid4()),
      "nativeThread":binding["native_thread"],"nativeTurn":turn or "runtime","action":action,"value":value or {}}
    async with client.request(method,server.make_url("/hollywood/v3/"+(path or "agents/"+a+"/contexts/reader")),
      json=body if method=="POST" else None,headers={"Authorization":"Bearer "+cfg["tokens"]["reader"]}) as r:
     return r.status,await r.json()
   status,pending=await call("pending");assert status==200,(status,pending)
   observed=c.execute("SELECT * FROM hb_context_material WHERE agent=%s AND value->>'roomObservation'='true'",(a,)).fetchall()
   assert len(observed)==1 and str(observed[0]["source"])==event
   source=observed[0];identity=str(source["id"])
   assert c.execute("SELECT count(*) AS n FROM hb_delivery").fetchone()["n"]==before
   assert c.execute("SELECT count(*) AS n FROM hb_context_material WHERE kind='need'").fetchone()["n"]==needs
   proof["initialHeadOnlyAndPassiveNoDeliveryOrNeed"]=True
   refresh(c,store,g)
   assert str(store.readable_event(c,g,private)["id"])==private
   proof["administratorVisibilityPreservedAndPeerPrivateAudienceFiltered"]=True
   assert c.execute("SELECT seq FROM hb_context_observation_cursor WHERE agent=%s",(a,)).fetchone()["seq"]>initial
   status,value=await call("reserve",{"materialIds":[identity]},"admission")
   assert status==200 and value["permit"]["state"]=="reserved",(status,value)
   permit=value["permit"]["id"];turn="qa-room-observation:"+str(uuid4())
   status,value=await call("begin",{"materialIds":[identity],"permitId":permit},turn)
   assert status==200,(status,value)
   proof["readOnlyRoomMembershipCanAdmitReaderWithoutExecution"]=True
   async def read(reference):
    offset=0;revision=None
    while True:
     status,value=await call("read",{"reference":reference,"offset":offset,**({"revision":revision} if revision else {})},turn)
     assert status==200,(status,value)
     revision=value["revision"];offset=value["nextOffset"]
     if offset is None:return
   await read(identity);await read(work)
   status,value=await call("accept",{"inputId":identity,"objective":"bad","acceptance":"bad"},turn)
   assert status==409,(status,value)
   status,value=await call("answer",{"inputId":identity,"body":"ack"},turn)
   assert status==409,(status,value)
   proof["observationCannotBecomeAddressedAssignmentOrRequiredAcknowledgement"]=True
   status,value=await call("room_list",{},turn);assert status==200,(status,value)
   assert any(x["id"]==room for x in value["rooms"])
   status,value=await call("room_history",{"roomId":room,"limit":5},turn)
   assert status==200,(status,value)
   assert not any(x["id"]==str(private) for x in value["events"])
   assert any(x["id"]==str(event) for x in value["events"])
   await read("event:"+str(event))
   status,value=await call("read",{"reference":"event:"+str(private)},turn);assert status==403,(status,value)
   status,value=await call("room_history",{"roomId":str(uuid4())},turn);assert status==403,(status,value)
   proof["canonicalRoomHistoryBoundedRetrievalAndPrivateAccessChecks"]=True
   with c.transaction(force_rollback=True):
    unicode_events=[store.event(c,g,room,"message","界🙂"*100) for _ in range(20)]
    status,page=await call("room_history",{"roomId":room,"limit":20},turn)
    assert status==200 and len(json.dumps(page,separators=(",",":"),sort_keys=True))<=6144,(status,page)
    seen=[x["id"] for x in page["events"]]
    assert len(seen)<20 and page.get("olderCursor")
    while len(set(seen)&set(unicode_events))<20:
     status,page=await call("room_history",{"roomId":room,"limit":20,"before":page["olderCursor"]},turn)
     assert status==200 and page["events"]
     assert not set(seen)&{x["id"] for x in page["events"]}
     seen.extend(x["id"] for x in page["events"])
    proof["multibyteHistoryBoundAndBackwardPaginationWithoutSkippedEvents"]=True
   status,value=await call("announce",{"sourceId":identity,"body":"Unpermitted acknowledgement","reason":"Fixture negative"},turn)
   assert status==403,(status,value)
   proof["readOnlyObservationCannotQueueOutwardCommunication"]=True
   status,value=await call("observe",{"observationId":identity,"workId":work,"workRevision":1,
    "body":"The announced feature affects this already accepted documentation responsibility; retain the original objective and verify source before use."},turn)
   assert status==200 and value["responseRequired"] is False and value["writerSteered"] is False,(status,value)
   row=c.execute("SELECT * FROM hb_context_material WHERE id=%s",(work,)).fetchone()
   assert row["value"]["objective"]=="Retain this exact original objective" and row["value"]["resumeRevision"]==1
   assert row["value"]["updates"][0]["sourceEventId"]==str(event)
   assert not c.execute("SELECT 1 FROM hb_context_material WHERE source=%s AND kind='need'",(event,)).fetchone()
   proof["semanticInterpretationUpdatesOriginalWorkWithoutReplyOrSteer"]=True
   status,value=await call("end",{"status":"completed","permitId":permit},turn)
   assert status==200,(status,value)
   for _ in range(2):
    status,value=await call("pending");assert status==200 and not value["pending"],(status,value)
   assert c.execute("SELECT count(*) AS n FROM hb_context_material WHERE agent=%s AND source=%s",(a,event)).fetchone()["n"]==1
   proof["durableCursorDeduplicatesWithoutRewakeOfHandledObservation"]=True
   # Offline arrival: no POST replay; the persisted cursor ingests original event.
   later=store.event(c,g,room,"message","A later event arrived while the reader was disconnected.")
   status,value=await call("pending");assert status==200 and len(value["pending"])==1
   c.execute("UPDATE hb_membership SET can_read=false WHERE principal=%s AND room=%s",(a,room))
   status,value=await call("pending");assert status==200 and not value["pending"],(status,value)
   assert c.execute("SELECT state FROM hb_context_material WHERE agent=%s AND source=%s",(a,later)).fetchone()["state"]=="unavailable"
   proof["offlineOriginalEventRecoveryAndRevocationWithoutPermissionResurrection"]=True
 finally:
  await server.close();store.pool=original_pool
 c.rollback()
 assert not c.execute("SELECT 1 FROM hb_event WHERE id=%s",(event,)).fetchone()
 proof["allFixtureWritesRolledBack"]=True
 path=Path("/aosp/development/lax-three-context-20261018-r01/qualification/room-observations-http-r31.json")
 path.write_text(json.dumps(proof,indent=2)+"\n");print(json.dumps(proof))
try:
 with store.pool.connection() as connection:asyncio.run(run(connection))
finally:store.close()
