"""Actual HTTP/PG logical claims and continuation mechanics. All fixtures roll back."""
import asyncio,contextlib,json,time
from pathlib import Path
from uuid import uuid4,uuid5,UUID
from aiohttp import ClientSession
from aiohttp.test_utils import TestServer
from psycopg.types.json import Jsonb
from hollywood_backbone.store import Store
from hollywood_backbone.server import application
from lax_connector.native_tool_catalog import CAPABILITIES
base=Path("/var/lib/lax-three-context-20261018-r01")
authority="f96e5615-0e6d-48d3-93b6-dc54393d5c2e";agent="f2363d21-e7da-4dc5-8749-6736d01d4f68"
setup=json.loads((base/"setup-intent.json").read_text())
cfg=json.loads((Path("/var/lib/harness")/authority/"purpose-agents"/agent/"home/agent.json").read_text())
store=Store(json.loads((base/"database.json").read_text()));store.migrate()
async def run(c):
 actor=store.actor(c,agent);human=store.actor(c,setup["credentials"]["Owner"]["principalId"])
 binding=c.execute("SELECT * FROM hb_purpose_context WHERE agent=%s AND purpose='writer'",(agent,)).fetchone()
 assert binding["active_turn"] is None
 fixtures=[]
 for label in ("A","B","C"):
  event=store.event(c,human,setup["globalRoomId"],"request","Rollback-only distinct responsibility "+label,recipient=actor["id"])
  delivery=str(uuid4());work=str(uuid5(UUID(event),"work"))
  c.execute("INSERT INTO hb_delivery(id,authority,event,agent) VALUES(%s,%s,%s,%s)",(delivery,authority,event,agent))
  c.execute("INSERT INTO hb_context_material(id,agent,kind,source,state,value) VALUES(%s,%s,'work',%s,'pending',%s)",
            (work,agent,event,Jsonb({"objective":"Fixture "+label,"acceptance":"Fixture only","resumeRevision":0})))
  fixtures.append((event,delivery,work))
 class Borrow:
  @contextlib.contextmanager
  def connection(self):
   with c.transaction():yield c
 oldpool=store.pool;store.pool=Borrow();server=TestServer(application(store),host="127.0.0.1")
 await server.start_server()
 from hollywood_backbone.context_capacity import resources
 counts=resources(c,cfg["hostId"])
 permit_count=c.execute("SELECT count(*) AS n FROM hb_context_permit").fetchone()["n"]
 proof={"schema":"lax-logical-work-frontier-http/v1","at":time.time(),
  "mode":"Real authenticated HTTP and PostgreSQL; synthetic request/material fixtures rolled back; no native launch/executor facts inserted",
  "modelsStarted":0}
 try:
  async with ClientSession() as client:
   async def call(action,value=None,operation=None):
    p={"runtimeProtocol":"lax-purpose-runtime/v3","operationId":operation or "qa-frontier:"+str(uuid4()),
       "action":action,"value":value or {},"nativeThread":binding["native_thread"],"nativeTurn":"bootstrap" if action=="bind" else "admission"}
    async with client.post(server.make_url("/hollywood/v3/agents/"+agent+"/contexts/writer"),
       json=p,headers={"Authorization":"Bearer "+cfg["tokens"]["writer"]}) as r:return r.status,await r.json()
   status,value=await call("bind",{"runtimeProtocol":"lax-purpose-runtime/v3","workScheduling":2})
   assert status==200 and value["workScheduling"]==2,(status,value)
   old_bind="bind:"+cfg["contextIds"]["writer"]+":"+binding["native_thread"]+":runtime-v3-work-updates-v1"
   status,value=await call("bind",{"runtimeProtocol":"lax-purpose-runtime/v3"},old_bind)
   assert status==409,(status,value)
   proof["cachedOldBindingCannotSilentlyDowngradeCurrentScheduling"]=True
   for _,delivery,work in fixtures[:2]:
    status,value=await call("admit",{"workId":work,"capabilities":CAPABILITIES},"qa-admit:"+work)
    assert status==200 and value["delivery"] and value["delivery"]["id"]==delivery and value["delivery"]["generation"]==1,(status,value)
   assert c.execute("SELECT count(*) AS n FROM hb_delivery WHERE id=ANY(%s::uuid[]) AND state='claimed'",([x[1] for x in fixtures[:2]],)).fetchone()["n"]==2
   assert resources(c,cfg["hostId"])==counts
   assert c.execute("SELECT count(*) AS n FROM hb_context_permit").fetchone()["n"]==permit_count
   proof["twoDistinctLogicalClaimsUseNoInferenceCapacityOrNativeStarts"]=True
   # Simulated material-consumption data exercises the scheduling query, not a
   # fabricated kernel/native outcome or a model cooperation claim.
   a=fixtures[0]
   c.execute("UPDATE hb_context_material SET value=value||%s WHERE id=%s",
     (Jsonb({"execution":{"status":"completed","nativeTurn":"fixture-material-only","resumeRevision":0}}),a[2]))
   status,value=await call("pending")
   assert status==200 and a[2] not in [x["id"] for x in value["pending"]] and fixtures[1][2] in [x["id"] for x in value["pending"]],(status,value)
   proof["consumedResponsibilityDoesNotBlockOtherPendingWork"]=True
   c.execute("UPDATE hb_context_material SET value=jsonb_set(value,'{resumeRevision}','1') WHERE id=%s",(a[2],))
   status,value=await call("pending")
   assert status==200 and a[2] in [x["id"] for x in value["pending"]],(status,value)
   proof["explicitNewContinuationMakesSameWorkRunnable"]=True
   # Exact original cancel before a native admission; another claim survives.
   req=server.make_url("/hollywood/v3/deliveries/"+a[1]+"/cancel")
   async with client.post(req,json={},headers={"Authorization":"Bearer "+setup["credentials"]["Owner"]["token"]}) as response:
    assert response.status==200,await response.text()
   status,value=await call("pending");assert status==200
   da=c.execute("SELECT * FROM hb_delivery WHERE id=%s",(a[1],)).fetchone()
   db=c.execute("SELECT * FROM hb_delivery WHERE id=%s",(fixtures[1][1],)).fetchone()
   assert da["state"]=="cancelled" and db["state"]=="claimed" and not db["cancellation_requested"]
   proof["neverAdmittedCancellationLeavesOtherLogicalClaimUntouched"]=True
   c.execute("UPDATE hb_delivery SET state='unknown' WHERE id=%s",(fixtures[1][1],))
   status,value=await call("admit",{"workId":fixtures[2][2],"capabilities":CAPABILITIES},"qa-admit:"+fixtures[2][2])
   assert status==200 and value["delivery"] is None and value["admission"]["reason"]=="unresolved_execution",(status,value)
   proof["unknownOriginalExecutionStillFencesNewOwnership"]=True
 finally:
  await server.close();store.pool=oldpool
 c.rollback()
 assert not c.execute("SELECT 1 FROM hb_delivery WHERE id=ANY(%s::uuid[])",([x[1] for x in fixtures],)).fetchone()
 proof["allFixturesRolledBack"]=True
 out=Path("/aosp/development/lax-three-context-20261018-r01/qualification/logical-work-frontier-http-r33.json")
 out.write_text(json.dumps(proof,indent=2)+"\n");print(json.dumps(proof))
try:
 with store.pool.connection() as c:asyncio.run(run(c))
finally:store.close()
