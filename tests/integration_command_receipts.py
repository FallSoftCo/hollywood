"""Authenticated PostgreSQL command evidence and current permissions; zero native models."""
import asyncio,json,os,secrets
from pathlib import Path
from uuid import uuid4
from aiohttp import ClientSession,web
from hollywood_backbone.bootstrap import initialize
from hollywood_backbone.store import Store
from hollywood_backbone.server import application

async def main():
 os.umask(0o077);root=Path(os.environ["COMMAND_EVIDENCE"]);root.mkdir(mode=0o700)
 db=json.loads(Path(os.environ["COMMAND_DATABASE_CONFIG"]).read_text());initialize(db,root/"enterprise","Command evidence HTTP qualification")
 setup=json.loads((root/"enterprise/installation-intent.json").read_text());people=setup["credentials"];room=setup["globalRoomId"];store=Store(db);runner=None;checks=[]
 async def start():
  nonlocal runner
  runner=web.AppRunner(application(store));await runner.setup();await web.TCPSite(runner,"127.0.0.1",18772).start()
 await start()
 async with ClientSession()as session:
  async def call(who,method,path,body=None,status=200):
   async with session.request(method,"http://127.0.0.1:18772/hollywood/v3/"+path,json=body,headers={"Authorization":"Bearer "+people[who]["token"]})as response:
    value=await response.json();assert response.status==status,(path,response.status,value,status);return value
  async def post(who,path,body,status=200):return await call(who,"POST",path,body,status)
  async def get(who,path,status=200):return await call(who,"GET",path,status=status)
  async def grant(read,revision):
   return await call("Owner","PUT","rooms/"+room+"/membership",{"principalId":people["Other"]["principalId"],"can_read":read,"can_post":read,"can_request":read,"revision":revision})
  def evidence(value,who,path,body):
   e=value["canonicalCommand"]
   assert e["schema"]=="hollywood-command/v1"and e["evidenceComplete"]
   assert e["actorId"]==people[who]["principalId"]and e["authorityId"]==setup["authorityId"]
   assert e["operationId"]==body["operationId"]and e["wireRequest"]=={"method":"POST","path":path,"body":body}
   assert e["eventId"]==value["event"]["id"]
   return e
  try:
   people["Other"]={"principalId":str(uuid4()),"token":secrets.token_urlsafe(40)}
   await post("Owner","principals",{"id":people["Other"]["principalId"],"name":"Other human","kind":"human","enrollmentCredential":people["Other"]["token"]});await grant(True,0)
   path="rooms/"+room+"/messages";body={"operationId":str(uuid4()),"kind":"request","body":"Metadata-only source; no native runtime is installed for this authority.","recipientId":people["Global"]["principalId"]}
   original=await post("Owner",path,body)
   command=await get("Owner","commands/"+body["operationId"]);e=evidence(command,"Owner",path,body);assert e["acceptedResult"]==original
   checks.append("Authenticated original wire retains omitted defaults and immutable accepted response")
   await post("Owner",path,{**body,"body":"Changed"},409);await get("Other","commands/"+body["operationId"],403)
   checks.append("Changed operation contents and other-principal receipt reads denied")
   delivery=original["deliveries"][0]["id"];await post("Owner","deliveries/"+delivery+"/cancel",{})
   changed=await get("Owner","commands/"+body["operationId"])
   assert changed["deliveries"][0]["state"]=="cancelled"and changed["canonicalCommand"]["acceptedResult"]["deliveries"][0]["state"]=="pending"
   checks.append("Current execution and frozen acceptance states remain distinct")
   source_body={**body,"operationId":str(uuid4()),"body":"New explicit metadata-only goal source"}
   source=await post("Owner",path,source_body)
   goal_path="rooms/"+room+"/goals";goal_body={"operationId":str(uuid4()),"id":str(uuid4()),"sourceEventId":source["event"]["id"],"title":"Command evidence","objective":"Inspect original command evidence without any model execution","acceptance":"Immutable original receipt and permissions","reviewBudget":1}
   goal=await post("Global",goal_path,goal_body);got=await get("Global","commands/"+goal_body["operationId"]);assert evidence(got,"Global",goal_path,goal_body)["acceptedResult"]==goal
   checks.append("Workflow original result is recovered without a live-state reconstruction")
   decision_path="rooms/"+room+"/decisions";decision_body={"id":str(uuid4()),"operationId":str(uuid4()),"key":"receipt-direction","revision":0,"goalId":None,"state":"active","body":"Retain exact acceptance evidence","sourceEventId":source["event"]["id"]}
   decision=await post("Global",decision_path,decision_body);got=await get("Global","commands/"+decision_body["operationId"]);assert evidence(got,"Global",decision_path,decision_body)["acceptedResult"]==decision
   checks.append("Decision receipts retain their actual typed accepted result")
   await runner.cleanup();await start()
   restored=await get("Owner","commands/"+body["operationId"]);assert restored["canonicalCommand"]==changed["canonicalCommand"]
   checks.append("API restart retains original acceptance without replay")
   other_body={"operationId":str(uuid4()),"kind":"message","body":"Original other human message"}
   await post("Other",path,other_body);await grant(False,1);await get("Other","commands/"+other_body["operationId"],403)
   checks.append("Current room revocation applies to original receipt reads")
   result={"passed":True,"checks":checks,"nativeModelsStarted":0,"productionChanged":False}
   (root/"result.json").write_text(json.dumps(result,indent=2));print(json.dumps(result))
  finally:await runner.cleanup();store.close()
if __name__=="__main__":asyncio.run(main())
