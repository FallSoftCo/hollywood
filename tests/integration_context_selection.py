"""Real authenticated HTTP/PG marked-context snapshots; synthetic material is disclosed."""
import asyncio,hashlib,json,os,secrets,time
from pathlib import Path
from uuid import uuid4
from aiohttp import web,ClientSession
from psycopg.types.json import Jsonb
from hollywood_backbone.server import application
from hollywood_backbone.store import Store,digest
from hollywood_backbone import context_selection

async def main():
 os.umask(0o077)
 root=Path(os.environ["SELECTION_EVIDENCE"]);root.mkdir(mode=0o700)
 def save(n,v):(root/n).write_text(json.dumps(v,indent=2,default=str)+"\n")
 ids={k:str(uuid4()) for k in ["authority","room","host","workspace","work"]}
 credentials={k:{"principalId":str(uuid4()),"token":secrets.token_urlsafe(40)} for k in ["Owner","Global"]}
 tokens={p:secrets.token_urlsafe(40) for p in ["reader","writer","responder"]}
 agent=credentials["Global"]["principalId"]
 save("intent.json",{"ids":ids,"credentials":credentials,"purposeTokens":tokens,"scope":"Synthetic context material only; real API transactions, authorization, concurrency and immutable receipts. No model or native lifecycle proof claimed."})
 store=Store(json.loads(Path(os.environ["SELECTION_DATABASE"]).read_text()));store.migrate()
 store.bootstrap(ids["authority"],"Finite context selection QA",{"globalRoomId":ids["room"],"credentials":credentials})
 runner=web.AppRunner(application(store));await runner.setup()
 site=web.TCPSite(runner,"127.0.0.1",0);await site.start()
 base="http://127.0.0.1:"+str(site._server.sockets[0].getsockname()[1])+"/hollywood/v3/"
 trace=[];checks=[]
 async with ClientSession() as client:
  async def call(method,path,body=None,who="Owner",status=200):
   token=tokens.get(who) or credentials[who]["token"]
   async with client.request(method,base+path,json=body,headers={"Authorization":"Bearer "+token}) as r:
    data=await r.json();trace.append({"method":method,"path":path,"who":who,"status":r.status,"response":data})
    save("trace.json",trace);assert r.status==status,(path,r.status,data)
    return data
  async def ctx(purpose,action,value=None,operation=None,status=200,who=None):
   return await call("POST",f"agents/{agent}/contexts/{purpose}",{
    "runtimeProtocol":"lax-purpose-runtime/v3","operationId":operation or str(uuid4()),
    "action":action,"nativeThread":"mechanical-"+purpose,"nativeTurn":"context-maintenance",
    "value":value or {}},who or purpose,status)
  try:
   await call("POST","placements",{"hostId":ids["host"],"name":"Disposable context test host","identityFingerprint":"synthetic-not-a-real-host","capabilities":{},"agentId":agent})
   bindings=[{"purpose":p,"contextId":str(uuid4()),"credentialDigest":digest(t)} for p,t in tokens.items()]
   await call("PUT","agents/"+agent+"/context-system",{"hostId":ids["host"],"workspaceId":ids["workspace"],"bindings":bindings})
   for p in tokens:
    await ctx(p,"bind",{"runtimeProtocol":"lax-purpose-runtime/v3","workScheduling":2})
   source=await call("POST","rooms/"+ids["room"]+"/messages",{
    "operationId":"snapshot-source-r270","kind":"request","recipientId":agent,
    "body":"Disclosed synthetic unfinished obligation. Preserve original identity; do not execute.","audience":"room"})
   event=source["event"]["id"]
   with store.pool.connection() as c:
    c.execute("INSERT INTO hb_context_material(id,agent,kind,source,state,value) VALUES(%s,%s,'work',%s,'pending',%s)",
      (ids["work"],agent,event,Jsonb({"objective":"Synthetic original objective","acceptance":"Mechanical snapshot only","progress":[]})))
   original=await ctx("writer","selection")
   keep={"selectionRevision":0,"reference":ids["work"],"materialRevision":1,"keep":True,"reason":"Retain original unfinished responsibility"}
   selected=await ctx("writer","select",keep,operation="select-original")
   assert selected["selectionRevision"]==1
   assert await ctx("writer","select",keep,operation="select-original")==selected
   await ctx("writer","select",keep,status=409)
   checks.append("purpose-owned selection CAS and original receipt dedup")
   await ctx("writer","select",{**keep,"selectionRevision":1,"reference":"role","keep":False},status=409)
   await ctx("responder","select",{**keep,"reference":ids["work"]},status=403)
   await ctx("writer","selection",who="reader",status=403)
   checks.append("role cannot be dropped; cross-purpose material/token access denied")
   await ctx("writer","rebuild_request",operation="request-original-rebuild")
   # Force a real independent commit between record reads in the worker thread.
   real=context_selection.material;injected=False
   def interleave(c,store_,actor,purpose,binding,turn,reference):
    nonlocal injected
    result=real(c,store_,actor,purpose,binding,turn,reference)
    if purpose=="writer" and not injected:
     injected=True
     with store.pool.connection() as other:
      other.execute("UPDATE hb_context_material SET revision=revision+1,value=value || %s WHERE id=%s",
        (Jsonb({"progress":["Concurrent new fact"]}),ids["work"]))
      other.execute("UPDATE hb_agent_role SET revision=revision+1,purpose='Concurrent changed role' WHERE principal=%s",(agent,))
    return result
   context_selection.material=interleave
   try:snapshot=await ctx("writer","rebuild_snapshot",{"materialIds":[ids["work"]]},operation="snapshot-original")
   finally:context_selection.material=real
   encoded="".join(x["content"] for x in snapshot["chunks"])
   assert hashlib.sha256(encoded.encode()).hexdigest()==snapshot["sha256"]
   content=json.loads(encoded)
   record={x["reference"]:x for x in content["records"]}
   assert record[ids["work"]]["revision"]==1 and record["role"]["revision"]==1
   assert content["obligations"][0]["source"]==event
   assert snapshot["semanticSufficiencyProved"] is False and snapshot["modelCalls"]==0
   checks.append("one repeatable-read cut across a concurrent role/work commit")
   original_receipt=await call("GET",f"agents/{agent}/contexts/writer/operations/snapshot-original",who="writer")
   assert original_receipt["sha256"]==snapshot["sha256"]
   fresh=await ctx("writer","rebuild_snapshot",{"materialIds":[ids["work"]]},operation="snapshot-fresh")
   assert fresh["sha256"]!=snapshot["sha256"]
   newer=json.loads("".join(x["content"] for x in fresh["chunks"]))
   records={x["reference"]:x for x in newer["records"]}
   assert records[ids["work"]]["revision"]==2 and records["role"]["revision"]==2
   checks.append("immutable old snapshot and fresh exact changed revisions")
   await ctx("writer","rebuild_installed",{"snapshotOperationId":"snapshot-fresh","sha256":"0"*64,"checkpointTurnId":"synthetic-metadata-only","nativeHistorySha256":"a"*64,"nativeHistoryBytes":1,"nativeActionsSha256":hashlib.sha256(b"[]").hexdigest()},status=409)
   # The server records runtime-reported provenance; it cannot inspect a host filesystem.
   installed=await ctx("writer","rebuild_installed",{"snapshotOperationId":"snapshot-fresh","sha256":fresh["sha256"],"checkpointTurnId":"synthetic-metadata-only","nativeHistorySha256":"a"*64,"nativeHistoryBytes":1,"nativeActionsSha256":hashlib.sha256(b"[]").hexdigest()},operation="installed-fixture")
   assert installed["generation"]==1 and "reported" in installed["verification"]
   with store.pool.connection() as c:
    assert c.execute("SELECT state FROM hb_delivery WHERE event=%s AND agent=%s",(event,agent)).fetchone()["state"]=="pending"
    assert c.execute("SELECT rebuild_requested FROM hb_purpose_context WHERE agent=%s AND purpose='writer'",(agent,)).fetchone()["rebuild_requested"] is None
   checks.append("generation receipt preserves original pending delivery; native proof remains explicitly host-reported")
   save("result.json",{"at":time.time(),"passed":True,"checks":checks,"modelCalls":0,"nativeCheckpoint":"Not exercised here; installed receipt used disclosed synthetic metadata","lifecycleFixtures":"Only one synthetic context Work and concurrent role/material update; no native execution or delivery completion fabricated"})
   print(json.dumps({"passed":True,"checks":len(checks),"modelCalls":0}))
  finally:
   await runner.cleanup();store.close()
if __name__=="__main__":asyncio.run(main())
