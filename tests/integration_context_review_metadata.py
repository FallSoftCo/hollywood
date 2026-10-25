"""Real HTTP/PostgreSQL readback; native threads/state are declared fixtures, no models."""
import argparse,asyncio,hashlib,json,os,secrets,sys
from pathlib import Path
from uuid import uuid4
import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from aiohttp import web,ClientSession

def uid():return str(uuid4())
async def main(args):
 os.umask(0o077);out=args.evidence;out.mkdir(mode=0o700,parents=True,exist_ok=False)
 if args.source_root:sys.path.insert(0,str(args.source_root))
 from hollywood_backbone.store import Store,digest
 from hollywood_backbone.server import application
 cfg=json.loads(args.database_config.read_text());schema="review_metadata_"+uuid4().hex
 (out/"intent.json").write_text(json.dumps({"schema":schema,"nativeBindingsAreFixtures":True,"modelsStarted":0,"productionChanged":False}))
 with psycopg.connect(**cfg) as c:c.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
 cfg={**cfg,"options":"-csearch_path="+schema};(out/"database-private.json").write_text(json.dumps(cfg))
 store=Store(cfg);store.migrate();plans=[]
 for n in range(2):
  p={"authorityId":uid(),"globalRoomId":uid(),"credentials":{k:{"principalId":uid(),"token":secrets.token_urlsafe(40)} for k in ("Owner","Global")}}
  store.bootstrap(p["authorityId"],"Metadata fixture "+str(n),p);plans.append(p)
 owner=plans[0]["credentials"]["Owner"];agent=plans[0]["credentials"]["Global"];foreign=plans[1]["credentials"]["Owner"]
 tokens={p:secrets.token_urlsafe(40) for p in ("reader","writer","responder")}
 second={"id":uid(),"token":secrets.token_urlsafe(40)};ordinary={"id":uid(),"token":secrets.token_urlsafe(40)}
 (out/"credentials-private.json").write_text(json.dumps({"plans":plans,"purpose":tokens,"second":second,"ordinary":ordinary}))
 runner=web.AppRunner(application(store),access_log=None);await runner.setup()
 site=web.TCPSite(runner,"127.0.0.1",0);await site.start()
 url="http://127.0.0.1:"+str(site._server.sockets[0].getsockname()[1])+"/hollywood/v3/"
 receipts=[];checks=[]
 async with ClientSession() as session:
  async def call(label,token,method,path,body=None,status=200):
   async with session.request(method,url+path,headers={"Authorization":"Bearer "+token},json=body) as response:
    result=await response.json()
    receipts.append({"caller":label,"method":method,"path":path,"status":response.status,"result":result})
    (out/"http-receipts-private.json").write_text(json.dumps(receipts,indent=2))
    assert response.status==status,(label,path,response.status,result)
    return result
  try:
   context_path="agents/"+agent["principalId"]+"/context-system"
   host_id=uid()
   await call("owner",owner["token"],"POST","placements",{"operationId":"metadata-placement",
    "hostId":host_id,"agentId":agent["principalId"],"name":"Declared unstarted metadata-only host",
    "identityFingerprint":"declared-fixture-no-host-controller","capabilities":{}})
   await call("owner",owner["token"],"PUT",context_path,{"operationId":"metadata-enroll","hostId":host_id,"workspaceId":"declared-no-installed-worker",
    "bindings":[{"purpose":p,"contextId":uid(),"credentialDigest":digest(t)} for p,t in tokens.items()]})
   before=await call("owner",owner["token"],"GET",context_path)
   assert len(before["bindings"])==3
   assert all(not b["contextReview"]["nativeReviewConfigured"] and not b["contextReview"]["nativeThreadBound"] for b in before["bindings"])
   assert all(b["contextReview"]["workerReadiness"]=="unverified" for b in before["bindings"])
   checks.append("Unbound/default-off profile is explicit and never healthy/ready")
   for p,t in tokens.items():
    await call(p,t,"POST","agents/"+agent["principalId"]+"/contexts/"+p,{"runtimeProtocol":"lax-purpose-runtime/v3",
     "operationId":"metadata-bind-"+p,"action":"bind","nativeThread":"declared-no-native-"+p,"nativeTurn":"fixture-bootstrap",
     "value":{"runtimeProtocol":"lax-purpose-runtime/v3","workScheduling":2,"toolCompletion":1,
              **({"nativeActionRetention":1,"manualSeedControl":1} if p=="writer" else {})}})
   view=await call("owner",owner["token"],"GET",context_path)
   writer=next(b["contextReview"] for b in view["bindings"] if b["purpose"]=="writer")
   assert writer["nativeReviewConfigured"] and writer["humanControlAuthorized"] and not writer["inferenceBusy"]
   assert writer["workerReadiness"]=="unverified"
   assert all(not b["contextReview"]["nativeReviewConfigured"] for b in view["bindings"] if b["purpose"]!="writer")
   checks.append("Configured Writer capability is distinct from purpose and worker liveness")
   draft_id=uid();base="agents/"+agent["principalId"]+"/contexts/writer/seed-drafts"
   await call("owner",owner["token"],"POST",base,{"id":draft_id,"operationId":"metadata-draft"})
   draft_path=base+"/"+draft_id
   preview=await call("owner",owner["token"],"GET",draft_path)
   await call("owner",owner["token"],"POST",draft_path+"/control",{"operationId":"metadata-prepare","action":"prepare",
    "revision":preview["draft"]["revision"],"canonicalSha256":preview["canonicalContentSha256"]})
   view=await call("owner",owner["token"],"GET",context_path)
   writer=next(b["contextReview"] for b in view["bindings"] if b["purpose"]=="writer")
   assert writer["reviewHeld"] and writer["rebuildPending"] and not writer["inferenceBusy"]
   assert writer["ownReview"]=={"draftId":draft_id,"state":"requested","revision":1}
   checks.append("Own exact original review can be recovered without re-preparing or inference admission")
   for human,name in [(second,"second"),(ordinary,"ordinary")]:
    await call("owner",owner["token"],"POST","principals",{"id":human["id"],"name":name+" metadata human","kind":"human","enrollmentCredential":human["token"]})
   # Declared same-authority administrator fixture: no model/enterprise enrollment proof.
   with psycopg.connect(**cfg) as c:c.execute("UPDATE hb_principal SET administrator=true WHERE id=%s",(second["id"],))
   for name,token in [("second-admin",second["token"]),("agent-admin",agent["token"])]:
    other=await call(name,token,"GET",context_path)
    item=next(b["contextReview"] for b in other["bindings"] if b["purpose"]=="writer")
    assert item["reviewHeld"] and item["ownReview"] is None
    assert draft_id not in json.dumps(other)
    assert item["humanControlAuthorized"]==(name=="second-admin")
   await call("foreign",foreign["token"],"GET",context_path,status=403)
   await call("nonadmin",ordinary["token"],"GET",context_path,status=403)
   purpose_view=await call("purpose",tokens["writer"],"GET",context_path)
   assert draft_id not in json.dumps(purpose_view)
   assert all(not b["contextReview"]["humanControlAuthorized"] and b["contextReview"]["ownReview"] is None for b in purpose_view["bindings"])
   await call("purpose-control",tokens["writer"],"GET",draft_path+"/control",status=403)
   checks.append("Other humans/agents see a hold without another human's draft; foreign/nonadmin refused; enrolled purpose retains metadata read but cannot access human control")
   with psycopg.connect(**cfg) as c:
    c.execute("UPDATE hb_purpose_context SET active_turn='declared-fixture-turn' WHERE agent=%s AND purpose='writer'",(agent["principalId"],))
   busy=await call("owner",owner["token"],"GET",context_path)
   assert next(b["contextReview"] for b in busy["bindings"] if b["purpose"]=="writer")["inferenceBusy"]
   with psycopg.connect(**cfg) as c:
    c.execute("UPDATE hb_purpose_context SET active_turn=NULL,accepting=true WHERE agent=%s AND purpose='writer'",(agent["principalId"],))
   accepting=await call("owner",owner["token"],"GET",context_path)
   assert next(b["contextReview"] for b in accepting["bindings"] if b["purpose"]=="writer")["inferenceBusy"]
   with psycopg.connect(**cfg) as c:
    c.execute("UPDATE hb_purpose_context SET accepting=false WHERE agent=%s AND purpose='writer'",(agent["principalId"],))
    c.execute("UPDATE hb_context_system SET enabled=false WHERE agent=%s",(agent["principalId"],))
    c.execute("UPDATE hb_principal SET active=false WHERE id=%s",(agent["principalId"],))
   disabled=await call("owner",owner["token"],"GET",context_path)
   item=next(b["contextReview"] for b in disabled["bindings"] if b["purpose"]=="writer")
   assert not item["humanControlAuthorized"] and not item["agentActive"] and not item["contextSystemEnabled"]
   assert item["ownReview"]["draftId"]==draft_id
   checks.append("Native busy/accepting is explicit; disabled agent still permits recovery of own review metadata")
   def fingerprint():
    with psycopg.connect(**cfg,row_factory=dict_row) as c:
     values={t:c.execute("SELECT * FROM "+t).fetchall() for t in ["hb_context_system","hb_purpose_context","hb_context_seed_draft","hb_context_seed_operation","hb_context_permit","hb_event","hb_command"]}
    return hashlib.sha256(json.dumps(values,default=str,sort_keys=True).encode()).hexdigest()
   original=fingerprint()
   for _ in range(3):await call("owner",owner["token"],"GET",context_path)
   assert fingerprint()==original
   with psycopg.connect(**cfg) as c:
    c.execute("UPDATE hb_token SET revoked=true WHERE principal=%s",(second["id"],))
   await call("revoked",second["token"],"GET",context_path,status=403)
   raw=json.dumps(receipts)
   assert not any(t in raw for t in [*tokens.values(),owner["token"],agent["token"],foreign["token"],second["token"],ordinary["token"]])
   checks.append("Readback mutates no state; revoked credential denied; bearer tokens never returned")
   result={"state":"actual_http_pg_context_review_metadata_passed","checks":checks,"httpRequests":len(receipts),"schema":schema,
      "nativeAndBusyBindings":"declared fixtures, not worker/model execution","modelsStarted":0,"productionChanged":False,
      "sourceRoot":str(args.source_root) if args.source_root else None}
   (out/"result.json").write_text(json.dumps(result,indent=2)+"\n");print(json.dumps(result),flush=True)
  finally:await runner.cleanup();store.close()
if __name__=="__main__":
 p=argparse.ArgumentParser();p.add_argument("--database-config",type=Path,required=True);p.add_argument("--evidence",type=Path,required=True)
 p.add_argument("--source-root",type=Path);asyncio.run(main(p.parse_args()))
