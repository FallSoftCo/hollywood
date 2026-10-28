"""Real authenticated HTTP/PostgreSQL external-response contract.

All actors are disposable metadata identities; no native processes or models
are created. Original submitted response events are reused, never recreated by
the resolution API. This is not semantic agent-work qualification.
"""
import argparse,asyncio,hashlib,json,os,secrets,sys,time
from pathlib import Path
from uuid import uuid4
import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from aiohttp import ClientSession,web

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument("--database-config",type=Path,required=True)
parser.add_argument("--output",type=Path,required=True)
parser.add_argument("--source-root",type=Path)
args=parser.parse_args()
if args.source_root:sys.path.insert(0,str(args.source_root))
from hollywood_backbone.bootstrap import initialize
from hollywood_backbone.server import application
from hollywood_backbone.store import Store,digest
os.umask(0o077)
args.output.mkdir(mode=0o700)
admin=json.loads(args.database_config.read_text())
database="harness_external_response_"+uuid4().hex[:14]
cfg={**admin,"dbname":database}
(args.output/"intent.json").write_text(json.dumps({"at":time.time(),"database":database,"scope":"Own disposable PG database, authenticated public API, no model/native process facts"}))
with psycopg.connect(**admin,autocommit=True) as c:c.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
calls=[];checks=[];store=None
async def main():
 global store
 initialize(cfg,args.output/"authority-a","External response QA A")
 initialize(cfg,args.output/"authority-b","External response QA B")
 a=json.loads((args.output/"authority-a/installation-intent.json").read_text())
 other=json.loads((args.output/"authority-b/installation-intent.json").read_text())
 store=Store(cfg);runner=web.AppRunner(application(store),access_log=None);await runner.setup()
 site=web.TCPSite(runner,"127.0.0.1",0);await site.start()
 url="http://127.0.0.1:"+str(site._server.sockets[0].getsockname()[1])+"/hollywood/v3/"
 owner=a["credentials"]["Owner"]["token"];sender=a["credentials"]["Global"];room=a["globalRoomId"]
 actors=[{"principalId":str(uuid4()),"token":secrets.token_urlsafe(40)} for _ in range(2)]
 b,c=actors
 try:
  async with ClientSession() as session:
   async def call(method,path,body=None,token=owner,status=200):
    async with session.request(method,url+path,json=body,headers={"Authorization":"Bearer "+token}) as response:
     value=await response.json();calls.append({"method":method,"path":path,"status":response.status})
     assert response.status==status,(method,path,response.status,status,value)
     return value
   async def enroll(actor,requests=True):
    await call("PUT","agents/"+actor["principalId"]+"/communication",{"operationId":"enroll:"+actor["principalId"],"credentialDigest":digest(actor["token"]),"allowRequests":requests})
   for actor in actors:
    await call("POST","principals",{"operationId":"create:"+actor["principalId"],"id":actor["principalId"],"name":"Unhosted QA "+actor["principalId"][:8],"kind":"agent","enrollmentCredential":actor["token"]})
    await call("PUT","rooms/"+room+"/membership",{"operationId":"join:"+actor["principalId"],"principalId":actor["principalId"],"can_read":True,"can_post":True,"can_request":True,"revision":0})
    await enroll(actor)
   await enroll(sender)
   async def task(label,reply=True,target=b):
    return await call("POST","rooms/"+room+"/messages",{"operationId":"request:"+label,"kind":"request","body":"Disposable diagnostic "+label,"recipientId":target["principalId"],"audience":"room","expectsReply":reply},token=sender["token"])
   async def answer(request,label,target=b,kind="message"):
    return await call("POST","rooms/"+room+"/messages",{"operationId":"answer:"+label,"kind":kind,"body":"Existing attributable QA answer "+label,"recipientId":sender["principalId"],"audience":"room","sourceEventId":request["event"]["id"]},token=target["token"])
   first=await task("first");reply=await answer(first,"first",kind="request")
   delivery=first["deliveries"][0]["id"];path="deliveries/"+delivery+"/external-response"
   payload={"operationId":"resolve:first","responseEventId":reply["event"]["id"]}
   absent=await call("GET",path+"?operationId=resolve:first",token=b["token"],status=404)
   assert absent["code"]=="EXTERNAL_RESPONSE_NOT_FOUND"
   result=await call("POST",path,payload,token=b["token"])
   assert result["executionState"]=="not_attested" and result["nativeExecutionAdmitted"] is False and result["communicationState"]=="answered"
   with store.pool.connection() as db:
    original=db.execute("select * from hb_delivery where id=%s",(delivery,)).fetchone()
    assert original["state"]=="completed" and str(original["result_event"])==reply["event"]["id"] and original["generation"]==0 and original["host"] is None
    notices=db.execute("select * from hb_event where data->>'replyToDelivery'=%s",(delivery,)).fetchall()
    assert len(notices)==1 and not notices[0]["data"].get("expectsReply") and notices[0]["data"]["executionState"]=="not_attested"
   assert await call("GET",path+"?operationId=resolve:first",token=b["token"])==result
   assert await call("POST",path,payload,token=b["token"])==result
   with store.pool.connection() as db:assert db.execute("select count(*) as n from hb_event where data->>'replyToDelivery'=%s",(delivery,)).fetchone()["n"]==1
   checks.append("Existing causal answer settles original generation0 communication; native execution is explicitly not attested. Discarded client result recovered by GET; idempotent duplicate verification creates no extra response/completion.")
   await call("POST",path,{**payload,"responseEventId":str(uuid4())},token=b["token"],status=409)
   await call("POST",path,{"operationId":"wrong-agent","responseEventId":reply["event"]["id"]},token=c["token"],status=403)
   await call("POST",path,{"operationId":"human-admin","responseEventId":reply["event"]["id"]},status=403)
   foreign=other["credentials"]["Global"]
   await call("PUT","agents/"+foreign["principalId"]+"/communication",{"operationId":"enroll:foreign","credentialDigest":digest(foreign["token"]),"allowRequests":True},token=other["credentials"]["Owner"]["token"])
   await call("POST",path,{"operationId":"foreign-agent","responseEventId":reply["event"]["id"]},token=foreign["token"],status=403)
   checks.append("Changed operation contents, another peer, human administrator and another authority cannot substitute for the addressed external agent.")
   second=await task("second");secondpath="deliveries/"+second["deliveries"][0]["id"]+"/external-response"
   await call("POST",secondpath,{"operationId":"wrong-cause","responseEventId":reply["event"]["id"]},token=b["token"],status=403)
   secondreply=await answer(second,"second")
   await call("POST","deliveries/"+second["deliveries"][0]["id"]+"/cancel")
   await call("POST",secondpath,{"operationId":"cancelled","responseEventId":secondreply["event"]["id"]},token=b["token"],status=409)
   checks.append("A different request's answer and a cancelled obligation are refused; cancellation is never relabeled successful execution.")
   ordinary=await task("not-reply-obligation",reply=False);ordinaryreply=await answer(ordinary,"not-reply")
   await call("POST","deliveries/"+ordinary["deliveries"][0]["id"]+"/external-response",{"operationId":"not-reply","responseEventId":ordinaryreply["event"]["id"]},token=b["token"],status=409)
   checks.append("An ordinary request without explicit expectsReply cannot use the response-settlement path.")
   limited=await task("revoked-post",target=c);limitedreply=await answer(limited,"revoked-post",target=c)
   await call("PUT","rooms/"+room+"/membership",{"operationId":"revoke-post","principalId":c["principalId"],"can_read":True,"can_post":False,"can_request":False,"revision":1})
   await call("POST","deliveries/"+limited["deliveries"][0]["id"]+"/external-response",{"operationId":"revoked-post","responseEventId":limitedreply["event"]["id"]},token=c["token"],status=403)
   checks.append("Current post permission is checked at terminal acceptance, after the answer was originally posted.")
   hosted=await task("newly-placed");hostedreply=await answer(hosted,"newly-placed")
   await call("POST","placements",{"operationId":"nonexecuting-placement","hostId":str(uuid4()),"agentId":b["principalId"],"name":"Metadata-only unavailable host","identityFingerprint":"r349-no-process-facts-fixture","capabilities":{"metadataOnly":True}})
   await call("POST","deliveries/"+hosted["deliveries"][0]["id"]+"/external-response",{"operationId":"hosted-refusal","responseEventId":hostedreply["event"]["id"]},token=b["token"],status=403)
   assert await call("GET",path+"?operationId=resolve:first",token=b["token"])==result
   checks.append("A newly placed actor cannot bypass native completion, while its already accepted external response remains readable.")
   await call("PUT","rooms/"+room+"/membership",{"operationId":"revoke-read","principalId":b["principalId"],"can_read":False,"can_post":False,"can_request":False,"revision":1})
   await call("GET",path+"?operationId=resolve:first",token=b["token"],status=403)
   checks.append("Revoking room read rights also refuses historical response receipt disclosure.")
   with store.pool.connection() as db:
    counts={t:db.execute("select count(*) as n from "+t).fetchone()["n"] for t in ["hb_executor","hb_agent_execution","hb_context_system","hb_native_effect","hb_work","hb_work_attempt"]}
    assert all(x==0 for x in counts.values()),counts
    original=db.execute("select * from hb_delivery where id=%s",(delivery,)).fetchone()
    assert original["native_thread"] is None and original["native_turn"] is None and original["host"] is None and original["generation"]==0
   output={"at":time.time(),"passed":True,"checks":checks,"httpCalls":calls,"nativeFacts":counts,"nativeProcessesOrModelsStarted":0,"scope":"Actual authenticated HTTP/PostgreSQL, disposable manual agent identities. Placement negative is metadata only. No native or semantic model proof; original live diagnostics not touched."}
   (args.output/"QUALIFIED.json").write_text(json.dumps(output,indent=2)+"\n")
   print(json.dumps({"passed":True,"checks":len(checks),"httpCalls":len(calls),"modelsStarted":0}))

 finally:
  await runner.cleanup()
try:
 asyncio.run(main())
except Exception as e:
 (args.output/"FAILURE.json").write_text(json.dumps({"type":type(e).__name__,"message":str(e),"calls":calls,"checks":checks},indent=2)+"\n")
 raise
finally:
 if store:store.close()
 with psycopg.connect(**admin,autocommit=True) as c:c.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(database)))

