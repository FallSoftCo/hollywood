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
  async def ctx(purpose,action,value=None,operation=None,status=200,who=None,native_turn="context-maintenance"):
   return await call("POST",f"agents/{agent}/contexts/{purpose}",{
    "runtimeProtocol":"lax-purpose-runtime/v3","operationId":operation or str(uuid4()),
    "action":action,"nativeThread":"mechanical-"+purpose,"nativeTurn":native_turn,
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
   # Real HTTP/PG receipt protocol with DISCLOSED synthetic native metadata.
   # No native process/file or model is being attested by this fixture.
   context_id=next(x["contextId"] for x in bindings if x["purpose"]=="writer")
   def native_proof(snapshot,operation):
    native={"threadId":"mechanical-writer","turnId":"synthetic-original-turn",
      "operationId":operation,"contextId":context_id,"purpose":"writer",
      "snapshotOperationId":operation,"snapshotReadPoint":snapshot["snapshotReadPoint"],
      "snapshotSha256":snapshot["sha256"],"nativeActionsSha256":hashlib.sha256(b"[]").hexdigest(),
      "selectionRevision":snapshot["selectionRevision"],"totalBytes":snapshot["totalBytes"],
      "previousWindowId":"019c0000-0000-7000-8000-000000000001",
      "windowId":"019c0000-0000-7000-8000-000000000002",
      "nativeHistoryBytes":10000,"nativeHistorySha256":"a"*64}
    preflight={"operationId":operation,"packetSha256":"b"*64,"replacementSha256":"c"*64,
      "prefixItemCount":3,"replacementItemCount":8,"replacementWireBytes":2000,
      "state":{"threadId":native["threadId"],"contextId":context_id,"purpose":"writer",
       "windowId":native["previousWindowId"],"historyVersion":12,
       "lastStartedTurnId":native["turnId"],"activeTurnId":None,"idle":True,"heldOperationId":None}}
    return {"schema":"lax-native-idle-context-installed/v1",
      "snapshotOperationId":"snapshot-native-idle-original",
      "nativeReceipt":native,"preflight":preflight}
   await ctx("writer","rebuild_request",operation="request-native-idle")
   native_snapshot=await ctx("writer","rebuild_snapshot",{"materialIds":[ids["work"]]},
     operation="snapshot-native-idle-original")
   proof=native_proof(native_snapshot,"synthetic-native-operation-original")
   await ctx("writer","rebuild_installed",proof,status=403,native_turn="synthetic-original-turn")
   contract=await ctx("writer","bind",{"runtimeProtocol":"lax-purpose-runtime/v3",
     "workScheduling":2,"nativeActionRetention":1})
   assert contract["nativeIdleContextReplacement"]==1
   await ctx("writer","rebuild_installed",proof,who="reader",status=403,
     native_turn="synthetic-original-turn")
   changed=json.loads(json.dumps(proof));changed["preflight"]["state"]["idle"]=False
   await ctx("writer","rebuild_installed",changed,status=409,native_turn="synthetic-original-turn")
   changed=json.loads(json.dumps(proof));changed["nativeReceipt"]["contextId"]=str(uuid4())
   await ctx("writer","rebuild_installed",changed,status=409,native_turn="synthetic-original-turn")
   checks.append("versioned idle receipt requires paired Writer, original purpose token and exact idle native cut")
   with store.pool.connection() as c:
    c.execute("UPDATE hb_context_material SET revision=revision+1,value=value || %s WHERE id=%s",
      (Jsonb({"progress":["Source changed after snapshot; stale native proof must refuse"]}),ids["work"]))
   await ctx("writer","rebuild_installed",proof,status=409,native_turn="synthetic-original-turn")
   checks.append("native installation reauthorizes exact current selected source revision")
   native_snapshot=await ctx("writer","rebuild_snapshot",{"materialIds":[ids["work"]]},
     operation="snapshot-native-idle-fresh")
   proof=native_proof(native_snapshot,"synthetic-native-operation-fresh")
   proof["snapshotOperationId"]="snapshot-native-idle-fresh"
   installed_native=await ctx("writer","rebuild_installed",proof,
     operation="installed-native-idle-original",native_turn="synthetic-original-turn")
   assert installed_native["schema"]==proof["schema"] and installed_native["status"]=="recorded"
   assert installed_native["nativeReceipt"]==proof["nativeReceipt"]
   assert installed_native["preflight"]==proof["preflight"] and installed_native["generation"]==2
   assert "checkpointTurnId" not in installed_native
   assert "not host filesystem attestation" in installed_native["verification"]
   assert await ctx("writer","rebuild_installed",proof,operation="installed-native-idle-original",
     native_turn="synthetic-original-turn")==installed_native
   original_native=await call("GET",
     f"agents/{agent}/contexts/writer/operations/installed-native-idle-original",who="writer")
   assert original_native==installed_native
   changed=json.loads(json.dumps(proof));changed["nativeReceipt"]["nativeHistorySha256"]="d"*64
   await ctx("writer","rebuild_installed",changed,operation="installed-native-idle-original",
     status=409,native_turn="synthetic-original-turn")
   with store.pool.connection() as c:
    native_binding=c.execute("SELECT rebuild_requested,rebuild_generation FROM hb_purpose_context "
      "WHERE agent=%s AND purpose='writer'",(agent,)).fetchone()
    assert native_binding["rebuild_requested"] is None and native_binding["rebuild_generation"]==2
    assert c.execute("SELECT state FROM hb_delivery WHERE event=%s AND agent=%s",
      (event,agent)).fetchone()["state"]=="pending"
   checks.append("truthful native-window receipt dedup/readback clears rebuild once and leaves pending Work untouched")

   # Frozen regression from production: individually bounded completed Work
   # records jointly exceed96KiB. Synthetic content/native lifecycle is explicit;
   # the real authenticated HTTP transaction and completion guard are exercised.
   oversized=[str(uuid4()),str(uuid4())]
   with store.pool.connection() as c:
    for ident in oversized:
     c.execute("INSERT INTO hb_context_material(id,agent,kind,source,state,value) "
       "VALUES(%s,%s,'work',%s,'complete',%s)",
       (ident,agent,event,Jsonb({"objective":"Disclosed archived Work fixture",
                               "retainedEvidence":"x"*55000})))
   for ident in oversized:
    marks=await ctx("writer","selection")
    await ctx("writer","select",{"selectionRevision":marks["selectionRevision"],
      "reference":ident,"materialRevision":1,"keep":True,
      "reason":"Disclosed combined-budget regression; no model or execution"})
   active="synthetic-budget-original-turn"
   with store.pool.connection() as c:
    c.execute("UPDATE hb_context_material SET value=value || %s,revision=revision+1 WHERE id=%s",
      (Jsonb({"execution":{"nativeThread":"mechanical-writer","nativeTurn":active,
                          "status":"running","resumeRevision":0}}),ids["work"]))
    c.execute("UPDATE hb_purpose_context SET active_turn=%s,accepting=true,"
      "active_material=%s::uuid[],tool_completion=1 WHERE agent=%s AND purpose='writer'",
      (active,[ids["work"]],agent))
    before=c.execute("SELECT value,revision FROM hb_context_material WHERE id=%s",
      (ids["work"],)).fetchone()
   rejected=await ctx("writer","rebuild_request",operation="oversized-rebuild-preview",
                      native_turn=active)
   assert rejected["requested"] is False
   assert rejected["code"]=="CONTEXT_REBUILD_BUDGET_EXCEEDED"
   assert rejected["totalBytes"]>rejected["limitBytes"]==98304
   assert {entry["reference"] for entry in rejected["records"] if entry["mandatory"]}=={"role",ids["work"]}
   assert all(set(entry)=={"reference","bytes","mandatory"} for entry in rejected["records"])
   assert "x"*100 not in json.dumps(rejected)
   with store.pool.connection() as c:
    after=c.execute("SELECT active_turn,accepting,rebuild_requested FROM hb_purpose_context "
      "WHERE agent=%s AND purpose='writer'",(agent,)).fetchone()
    assert after=={"active_turn":active,"accepting":True,"rebuild_requested":None}
    assert before==c.execute("SELECT value,revision FROM hb_context_material WHERE id=%s",
      (ids["work"],)).fetchone()
   checks.append("oversized Writer preview retains accepting original turn and unchanged continuation")
   marks=await ctx("writer","selection",native_turn=active)
   await ctx("writer","select",{"selectionRevision":marks["selectionRevision"],
     "reference":oversized[0],"materialRevision":1,"keep":False,
     "reason":"Unpin one optional archived source; preserve durable original"},
     native_turn=active)
   accepted=await ctx("writer","rebuild_request",operation="fitting-rebuild-request",
                      native_turn=active)
   assert accepted["requested"] is True
   assert accepted["inferenceControl"]["disposition"]=="complete"
   assert await call("GET",f"agents/{agent}/contexts/writer/operations/oversized-rebuild-preview",
                     who="writer")==rejected
   with store.pool.connection() as c:
    state=c.execute("SELECT active_turn,accepting,rebuild_requested FROM hb_purpose_context "
      "WHERE agent=%s AND purpose='writer'",(agent,)).fetchone()
    assert state=={"active_turn":active,"accepting":False,"rebuild_requested":"fitting-rebuild-request"}
    assert c.execute("SELECT value,revision FROM hb_context_material WHERE id=%s",
      (ids["work"],)).fetchone()["revision"]==before["revision"]+1
    assert c.execute("SELECT count(*) AS n FROM hb_context_material "
      "WHERE id=ANY(%s::uuid[]) AND state='complete'",(oversized,)).fetchone()["n"]==2
   checks.append("existing selection CAS fits request; immutable rejection and archived sources retained")

   # Mechanical lifecycle fixture: a marked optional response later becomes
   # suppressed. Its pointer survives; its old body is no longer responder input.
   need_id=str(uuid4())
   with store.pool.connection() as c:
    c.execute("INSERT INTO hb_context_material(id,agent,kind,source,state,value) VALUES(%s,%s,'need',%s,'ready',%s)",
      (need_id,agent,event,Jsonb({"body":"RETIRED_RESPONSE_CONTENT_MUST_NOT_RETURN"})))
   await ctx("responder","select",{"selectionRevision":0,"reference":need_id,"materialRevision":1,
      "keep":True,"reason":"Retain actual lifecycle fixture before suppression"})
   with store.pool.connection() as c:
    c.execute("UPDATE hb_context_material SET state='suppressed',revision=revision+1 WHERE id=%s",(need_id,))
   await ctx("responder","rebuild_request")
   retained=await ctx("responder","rebuild_snapshot",{"materialIds":[]})
   retained_content=json.loads("".join(x["content"] for x in retained["chunks"]))
   marked=next(x for x in retained_content["records"] if x["reference"]==need_id)
   assert marked=={"reference":need_id,"access":"unavailable",
      "meaning":"Retained reference is no longer visible to this purpose. No content or authority was restored."}
   assert "RETIRED_RESPONSE_CONTENT_MUST_NOT_RETURN" not in json.dumps(retained)
   assert any(x["reference"]==need_id for x in retained_content["selection"]["marks"])
   await ctx("responder","rebuild_snapshot",{"materialIds":[need_id]},status=403)
   checks.append("suppressed retained response preserves unavailable pointer, not content or mandatory-next authority")
   save("result.json",{"at":time.time(),"passed":True,"checks":checks,"modelCalls":0,"nativeCheckpoint":"Not exercised here; legacy and native-window receipts used disclosed synthetic metadata","lifecycleFixtures":"Only one synthetic context Work and concurrent role/material update; no native execution or delivery completion fabricated"})
   print(json.dumps({"passed":True,"checks":len(checks),"modelCalls":0}))
  finally:
   await runner.cleanup();store.close()
if __name__=="__main__":asyncio.run(main())
