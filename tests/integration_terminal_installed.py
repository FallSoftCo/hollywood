"""Actual authenticated HTTP/PG terminal-checkpoint acknowledgement.

Disposable simulated native metadata, no model or native process. Real native
checkpoint I/O is qualified separately against the preserved original history.
"""
import argparse,asyncio,hashlib,json,os,secrets,sys,time
from pathlib import Path
from uuid import uuid4
import psycopg
from psycopg import sql
from aiohttp import ClientSession,web
from psycopg.types.json import Jsonb

p=argparse.ArgumentParser();p.add_argument("--database-config",type=Path,required=True);p.add_argument("--output",type=Path,required=True);p.add_argument("--source-root",type=Path);a=p.parse_args()
if a.source_root:sys.path.insert(0,str(a.source_root))
from hollywood_backbone.server import application
from hollywood_backbone.store import Store,digest
os.umask(0o077);a.output.mkdir(mode=0o700);admin=json.loads(a.database_config.read_text());database="harness_install_observe_"+uuid4().hex[:14];cfg={**admin,"dbname":database}
(a.output/"intent.json").write_text(json.dumps({"database":database,"scope":__doc__}))
with psycopg.connect(**admin,autocommit=True) as c:c.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
calls=[];checks=[];store=None
async def main():
 global store
 from hollywood_backbone import context_installed_recovery
 real_acknowledge=context_installed_recovery.acknowledge
 def traced_acknowledge(*args,**kwargs):
  try:return real_acknowledge(*args,**kwargs)
  except Exception as error:
   with (a.output/"refusals.jsonl").open("a") as f:f.write(json.dumps({"type":type(error).__name__,"message":str(error)})+"\n")
   raise
 context_installed_recovery.acknowledge=traced_acknowledge
 store=Store(cfg);store.migrate()
 ids={k:str(uuid4()) for k in ["authority","room","host","workspace","permit","context","event"]}
 credentials={k:{"principalId":str(uuid4()),"token":secrets.token_urlsafe(40)} for k in ["Owner","Global"]}
 agent=credentials["Global"]["principalId"];tokens={k:secrets.token_urlsafe(40) for k in ["reader","writer","responder"]};contexts={k:str(uuid4()) for k in tokens}
 store.bootstrap(ids["authority"],"Terminal installed QA",{"globalRoomId":ids["room"],"credentials":credentials})
 (a.output/"private-fixture.json").write_text(json.dumps({"ids":ids,"credentials":credentials,"tokens":tokens,"contexts":contexts}))
 runner=web.AppRunner(application(store),access_log=None);await runner.setup();site=web.TCPSite(runner,"127.0.0.1",0);await site.start();url="http://127.0.0.1:"+str(site._server.sockets[0].getsockname()[1])+"/hollywood/v3/"
 try:
  async with ClientSession() as session:
   async def call(method,path,body=None,token=None,status=200):
    async with session.request(method,url+path,json=body,headers={"Authorization":"Bearer "+(token or credentials["Owner"]["token"])}) as res:
     value=await res.json();calls.append({"method":method,"path":path,"action":(body or {}).get("action"),"status":res.status})
     assert res.status==status,(method,path,res.status,status,value)
     return value
   await call("POST","placements",{"hostId":ids["host"],"name":"Simulated metadata only","identityFingerprint":"no-native-host-started","capabilities":{},"agentId":agent})
   await call("PUT","agents/"+agent+"/context-system",{"hostId":ids["host"],"workspaceId":ids["workspace"],"bindings":[{"purpose":k,"contextId":contexts[k],"credentialDigest":digest(tokens[k])} for k in tokens]})
   msg=await call("POST","rooms/"+ids["room"]+"/messages",{"operationId":"source","kind":"request","body":"Disposable pending original input","recipientId":agent,"audience":"room"})
   source=msg["event"]["id"];thread="fixture-thread";turn="fixture-original";path="agents/"+agent+"/contexts/reader";op="fixture-native-install"
   # Declared simulated initial native-bound state; no runtime/process facts.
   with store.pool.connection() as db:
    material=db.execute("select * from hb_context_material where agent=%s and source=%s and kind='input'",(agent,source)).fetchone();assert material
    mids=[str(material["id"])]
    db.execute("update hb_purpose_context set native_thread=%s,active_turn=%s,active_material=%s::uuid[],accepting=true where agent=%s and purpose='reader'",(thread,turn,mids,agent))
    db.execute("""insert into hb_context_permit(id,agent,purpose,host,context_id,native_thread,native_turn,operation,fingerprint,material_ids,role_revision,queue_class,state)
      values(%s,%s,'reader',%s,%s,%s,%s,'fixture-permit','simulated',%s::uuid[],1,'worker','running')""",(ids["permit"],agent,ids["host"],contexts["reader"],thread,turn,mids))
   async def ctx(action,value,operation,who="reader",status=200,th=thread):
    return await call("POST",path,{"runtimeProtocol":"lax-purpose-runtime/v3","operationId":operation,"action":action,"nativeThread":th,"nativeTurn":turn,"value":value},token=tokens.get(who) or credentials[who]["token"],status=status)
   common={"operationId":op,"contextId":contexts["reader"],"permitId":ids["permit"],"materialIds":mids}
   snapshot=await ctx("active_snapshot",{**common,"windowId":"old","historyVersion":0,"reason":"contextLimit","phase":"preTurn"},op)
   value={**common,"snapshotOperationId":op,"snapshotReadPoint":snapshot["snapshotReadPoint"],"snapshotSha256":snapshot["sha256"],"nativeActionsSha256":hashlib.sha256(b"[]").hexdigest(),"selectionRevision":snapshot["selectionRevision"],"totalBytes":snapshot["totalBytes"],"previousWindowId":"old","windowId":"new","nativeHistoryBytes":1000,"nativeHistorySha256":"a"*64,"nativeTerminalStatus":"failed"}
   ack="active-installed:"+hashlib.sha256(op.encode()).hexdigest()
   await ctx("active_installed_reconcile",value,ack,status=409)
   await ctx("end",{"status":"failed","permitId":ids["permit"]},"context-end:"+turn)
   before=None
   with store.pool.connection() as db:
    before=db.execute("select * from hb_purpose_context where agent=%s and purpose='reader'",(agent,)).fetchone()
   for key,bad in [("snapshotSha256","b"*64),("nativeTerminalStatus","completed"),("permitId",str(uuid4())),("materialIds",[])]:
    await ctx("active_installed_reconcile",{**value,key:bad},ack,status=409)
   await ctx("active_installed_reconcile",value,ack,who="Owner",status=403)
   await ctx("active_installed_reconcile",value,ack,who="writer",status=403)
   checks.append("Active context, changed snapshot digest/status/permit/material, human admin and wrong purpose all refused before receipt creation.")
   result=await ctx("active_installed_reconcile",value,ack)
   assert result["schema"]=="lax-active-context-installed/v1" and result["status"]=="recorded" and result["availabilityRestored"] is False and result["newExecutionAdmitted"] is False
   observed=await call("GET",path+"/operations/"+ack,token=tokens["reader"]);assert observed==result
   assert await ctx("active_installed_reconcile",value,ack)==result
   with store.pool.connection() as db:
    after=db.execute("select * from hb_purpose_context where agent=%s and purpose='reader'",(agent,)).fetchone();assert before==after
    m=db.execute("select * from hb_context_material where id=%s",(mids[0],)).fetchone();assert m["state"]=="pending" and m["revision"]==material["revision"]
    assert db.execute("select count(*) as n from hb_context_operation where agent=%s and purpose='reader' and operation=%s",(agent,ack)).fetchone()["n"]==1
    for table in ["hb_work","hb_work_attempt","hb_executor","hb_native_effect"]:
     assert db.execute("select count(*) as n from "+table).fetchone()["n"]==0,table
   checks.append("Terminal original snapshot gets one durable acknowledgement; lost-result GET and duplicate receipt agree. Binding/reads/generation/material unchanged; no Work/native execution created.")
   await ctx("active_installed_reconcile",{**value,"windowId":"other"},ack,status=409)
   checks.append("Existing operation rejects changed installed identity. Failed inference remains failed; pending input is not marked handled.")
   out={"at":time.time(),"checks":checks,"httpRequests":len(calls),"modelCalls":0,"nativeProcesses":0,"nativeDurabilityClaim":False,"fixture":"Simulated native metadata; authenticated real HTTP/PostgreSQL","passed":True}
   (a.output/"QUALIFIED.json").write_text(json.dumps(out,indent=2));print(json.dumps(out))
 finally:await runner.cleanup();store.pool.close()
try:asyncio.run(main())
finally:
 (a.output/"http-trace.json").write_text(json.dumps(calls,indent=2))
 with psycopg.connect(**admin,autocommit=True) as c:c.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database)))
