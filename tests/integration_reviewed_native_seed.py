"""Real human HTTP/PostgreSQL + SDK/native/WebSocket reviewed-window journey.

Provider output and initial material/native bindings are declared fixtures.
No semantic model, production, active-turn or phone qualification is claimed.
"""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb
from aiohttp import ClientSession, WSMsgType, web
from hollywood_backbone.store import Store, digest
from hollywood_backbone.server import application
from lax_connector.rpc import AppServer
from lax_connector.idle_context_rebuild import IdleContextRebuild
from lax_connector.reviewed_context_control import ReviewedContextControl


def uid():
    return str(uuid4())


async def main(args):
    os.umask(0o077)
    out = args.evidence
    out.mkdir(mode=0o700, parents=True, exist_ok=False)
    cfg = json.loads(args.database_config.read_text())
    schema = "native_review_" + uuid4().hex
    (out/"intent.json").write_text(json.dumps({"schema": schema, "fixture": True,
        "productionChanged": False, "externalModels": False}, indent=2))
    with psycopg.connect(**cfg) as c:
        c.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    cfg = {**cfg, "options": "-csearch_path=" + schema}
    (out/"database-private.json").write_text(json.dumps(cfg))
    store = Store(cfg); store.migrate()
    plans = []
    for i in range(2):
        plan = {"globalRoomId": uid(), "credentials": {n: {"principalId": uid(),
                "token": __import__("secrets").token_urlsafe(40)} for n in ("Owner","Global")}}
        store.bootstrap(uid(), "Native reviewed fixture "+str(i), plan); plans.append(plan)
    owner, foreign = [p["credentials"]["Owner"]["token"] for p in plans]
    admin_agent = plans[0]["credentials"]["Global"]["token"]
    room = plans[0]["globalRoomId"]
    peer, host, material = uid(), uid(), uid()
    tokens = {n: __import__("secrets").token_urlsafe(40) for n in ("reader","writer","responder")}
    contexts = {n: uid() for n in tokens}
    (out/"credentials-private.json").write_text(json.dumps({"plans":plans,"purposeTokens":tokens}))
    received, http_receipts, native_events, checks = [], [], [], []
    process_records = []
    stop, complete = asyncio.Event(), asyncio.Event()
    state, home = out/"state", out/"home"; state.mkdir(); home.mkdir()
    r = SimpleNamespace(config={"agentId": peer,"contextIds":contexts,"nativeActionRetention":1,
          "manualSeedControl":1,"contextRebuildEnabled":True}, purpose="writer", state=state,
          active=False, accepting=False, closing=False, failed=False, retained_context_contract=True,
          effect_lock=asyncio.Lock(), automatic=SimpleNamespace(blocked=False))
    r.trace = lambda event, data: native_events.append({"event":event,"data":data})
    r.agent = SimpleNamespace(stop=stop)
    servers = []
    session = ClientSession()

    async def serve(app):
        runner=web.AppRunner(app,access_log=None);await runner.setup()
        site=web.TCPSite(runner,"127.0.0.1",0);await site.start();servers.append(runner)
        return "http://127.0.0.1:"+str(site._server.sockets[0].getsockname()[1])

    async def response_events():
        n=len(received)
        return [{"type":"response.created","response":{"id":"fixture-"+str(n)}},
          {"type":"response.output_item.done","item":{"type":"message","role":"assistant","id":"m"+str(n),
           "content":[{"type":"output_text","text":"Declared scripted native completion"}]}},
          {"type":"response.completed","response":{"id":"fixture-"+str(n),"usage":{
           "input_tokens":0,"input_tokens_details":None,"output_tokens":0,
           "output_tokens_details":None,"total_tokens":0}}}]

    async def provider(request):
        ws=web.WebSocketResponse()
        if ws.can_prepare(request).ok:
            await ws.prepare(request)
            async for message in ws:
                if message.type==WSMsgType.TEXT:
                    received.append(json.loads(message.data))
                    for event in await response_events():await ws.send_json(event)
            return ws
        raise web.HTTPBadRequest()

    async def event(method, params):
        native_events.append({"method":method,"params":params})
        if method=="turn/completed":
            assert params["turn"]["status"]=="completed",params
            complete.set()

    async def callback(method, params):
        if method=="thread/contextWindow/idleInstalled":
            return await r.automatic.idle.callback(params)
        assert method=="thread/contextWindow/reconcile",method
        return await r.automatic.idle.reconcile(params)

    async def submitted(method, params, ok):
        await r.automatic.idle.submitted(params,ok)

    async def spawned(rpc):
        process_records.append({"pid":rpc.process.pid,"ticks":
          Path("/proc/"+str(rpc.process.pid)+"/stat").read_text().rsplit(")",1)[1].split()[19]})

    try:
        backend=await serve(application(store))
        provider_app=web.Application()
        provider_app.router.add_route("*","/{path:.*}",provider)
        provider_url=await serve(provider_app)
        (home/"config.toml").write_text('model="mock-model"\napproval_policy="never"\n'
          'sandbox_mode="read-only"\nmodel_provider="review_fixture"\n[model_providers.review_fixture]\n'
          'name="Declared WebSocket protocol fixture"\nwire_api="responses"\nsupports_websockets=true\n'
          'base_url="'+provider_url+'/v1"\nrequest_max_retries=0\nstream_max_retries=0\n')
        async def call(token,method,path,data=None,status=200):
            async with session.request(method,backend+"/hollywood/v3/"+path,
                   headers={"Authorization":"Bearer "+token},json=data) as response:
                result=await response.json()
                http_receipts.append({"method":method,"path":path,"status":response.status,"result":result})
                (out/"http-private.json").write_text(json.dumps(http_receipts,indent=2))
                assert response.status==status,(method,path,response.status,result)
                return result

        async def purpose(name,action,value=None,*,operation=None,turn=None,status=200):
            return await call(tokens[name],"POST",f"agents/{peer}/contexts/{name}",{
              "runtimeProtocol":"lax-purpose-runtime/v3","operationId":operation or uid(),"action":action,
              "nativeThread":r.thread if name=="writer" else "declared-no-process-"+name,
              "nativeTurn":turn or "context-maintenance","value":value or {}},status=status)

        fault = {"cancelPath": None, "mutateAfterAdmission": False}
        async def request(action,value=None,*,operation=None,turn=None):
            if action == "rebuild_snapshot" and fault["cancelPath"]:
                path = fault["cancelPath"]; fault["cancelPath"] = None
                current = await call(owner,"GET",path+"/control")
                await call(owner,"POST",path+"/control",{"operationId":"cancel-during-prepare",
                  "action":"cancel","revision":current["control"]["revision"]})
            result = await purpose("writer",action,value,operation=operation,turn=turn)
            if action == "seed_apply" and fault["mutateAfterAdmission"]:
                r.config["changedAfterAdmission"] = True
            return result
        r.request=request
        r.agent.http=lambda name,method,path:call(tokens[name],method,path)

        await call(owner,"POST","principals",{"id":peer,"name":"Unstarted disposable native-review agent",
          "kind":"agent","enrollmentCredential":__import__("secrets").token_urlsafe(40)})
        await call(owner,"PUT",f"rooms/{room}/membership",{"principalId":peer,
          "can_read":True,"can_post":True,"can_request":True,"revision":0})
        await call(owner,"POST","placements",{"hostId":host,"name":"Disposable protocol-only native host",
          "identityFingerprint":"declared-fixture-no-installed-worker","capabilities":{},"agentId":peer})
        await call(owner,"PUT",f"agents/{peer}/context-system",{"hostId":host,
          "workspaceId":"disposable-no-daemon","bindings":[{"purpose":n,"contextId":contexts[n],
             "credentialDigest":digest(tokens[n])} for n in tokens]})
        r.rpc=AppServer(str(args.binary),home,state/"rpc",event,callback,
                       on_submission=submitted,on_spawn=spawned,isolated_process_group=True)
        await r.rpc.start()
        started=await r.rpc.call("thread/start",{"automaticContextRefill":{
          "type":"canonicalRetainedActions","contextId":contexts["writer"],"purpose":"writer"},"cwd":str(out)})
        r.thread=started["thread"]["id"];history=Path(started["thread"]["path"])
        r.automatic.idle=IdleContextRebuild(r)
        bridge=ReviewedContextControl(r)
        for n in ("writer","reader"):
            bind={"runtimeProtocol":"lax-purpose-runtime/v3","workScheduling":2,"toolCompletion":1}
            if n=="writer":bind.update(nativeActionRetention=1,manualSeedControl=1)
            result=await purpose(n,"bind",bind,operation="fixture-bind-"+n,turn="bootstrap")
            if n=="writer":assert result["manualSeedControl"]==1

        async def turn(text):
            complete.clear()
            await r.rpc.call("turn/start",{"threadId":r.thread,
              "input":[{"type":"text","text":text,"textElements":[]}]})
            await asyncio.wait_for(complete.wait(),45)

        await turn("UNSELECTED_ORIGINAL_WINDOW_CANARY -- declared native recording fixture")
        original=history.read_bytes()
        source=await call(owner,"POST",f"rooms/{room}/messages",{"operationId":"declared-source",
          "kind":"message","body":"Declared closing-window material fixture, not model-discovered.",
          "audience":"room"})
        with store.pool.connection() as c:
            c.execute("""INSERT INTO hb_context_material(id,agent,kind,source,state,value)
              VALUES(%s,%s,'work',%s,'complete',%s)""",(material,peer,source["event"]["id"],
              Jsonb({"fact":"SELECTED_NAUTICAL_LOCALE","correction":"KEEP_ORIGINAL_LEDGER_IDS"})))

        base=f"agents/{peer}/contexts/writer"
        async def draft(pin=False,prepare=True):
            identity=uid();path=base+"/seed-drafts/"+identity
            created=await call(owner,"POST",base+"/seed-drafts",{"id":identity,"operationId":"create"})
            revision=created["draft"]["revision"]
            if pin:
                record=(await call(owner,"GET",base+"/seed-sources?reference="+material))["record"]
                marked=await call(owner,"PUT",path+"/marks",{"operationId":"pin-finding",
                  "revision":revision,"reference":material,"sourceRevision":record["revision"],
                  "sourceSha256":record["sha256"],"keep":True,"reason":"Retain new fact and user correction"})
                revision=marked["draft"]["revision"]
            preview=await call(owner,"GET",path)
            if not prepare:
                return path, preview
            prepared=await call(owner,"POST",path+"/control",{"operationId":"prepare",
              "action":"prepare","revision":revision,"canonicalSha256":preview["canonicalContentSha256"]})
            return path,prepared

        path,summary=await draft(pin=True)
        await call(admin_agent,"POST",path+"/control",{"operationId":"agent-cannot-approve",
          "action":"approve","revision":1,"approvalDigest":"0"*64},status=403)
        await call(foreign,"GET",path+"/control",status=403)
        await purpose("writer","reserve",{"materialIds":[material]},operation="writer-held",status=409)
        incoming=await call(owner,"POST",f"rooms/{room}/messages",{"operationId":"queued-during-review",
          "kind":"request","body":"Disposable pending input; no worker should execute it.",
          "audience":"direct","recipientId":peer})
        pending=await purpose("reader","pending")
        assert any(x["id"]==incoming["event"]["id"] for x in pending["pending"])
        with store.pool.connection() as c:
            assert c.execute("SELECT count(*) AS n FROM hb_context_permit WHERE state IN ('reserved','running')").fetchone()["n"]==0
        await bridge.advance(summary)
        status=await call(owner,"GET",path+"/control")
        assert status["control"]["state"]=="prepared"
        prepared=r.automatic.idle.pending
        assert prepared["approvalDigest"]==status["control"]["approvalDigest"]
        assert history.read_bytes()==original and len(received)==1
        content=await call(owner,"GET",path+"/control/content")
        assert content["snapshot"]["sha256"]==prepared["snapshot"]["sha256"]
        assert "SELECTED_NAUTICAL_LOCALE" in json.dumps(content)
        checks.append("Authenticated human pins/frozen sources and actual native preparation agree; Writer admission held with no inference slot; Reader input stays pending")
        try:
            await r.automatic.idle.apply_prepared(prepared,approved_digest=prepared["approvalDigest"])
        except RuntimeError as error:assert "admission callback required" in str(error)
        else:raise AssertionError("Paired native profile accepted no Hollywood dispatch gate")
        await purpose("writer","seed_apply",{"draftId":summary["draftId"],
          "approvalDigest":prepared["approvalDigest"],"preflight":prepared["preflight"]},
          operation="cannot-dispatch-without-human",status=409)
        payload={"operationId":"human-approve","action":"approve",
          "revision":status["control"]["revision"],"approvalDigest":prepared["approvalDigest"]}
        await call(owner,"POST",path+"/control",{**payload,"approvalDigest":"0"*64},status=409)
        approved=await call(owner,"POST",path+"/control",payload)
        receipt=await call(owner,"GET",path+"/operations/human-approve")
        assert receipt==approved
        await bridge.advance(approved)
        installed=await call(owner,"GET",path+"/control")
        assert installed["control"]["state"]=="installed" and installed["control"]["generation"]==1
        assert installed["control"]["nativeReceipt"]==prepared["installed"]
        archived = await call(owner,"GET",path+"/control/content")
        assert archived["historical"] is True and archived["snapshot"] == content["snapshot"]
        await call(owner,"POST",path+"/control",{"operationId":"cannot-cancel-installed",
          "action":"cancel","revision":installed["control"]["revision"]},status=409)
        await turn("Continue from the reviewed fresh window")
        assert len(received)==2
        body=json.dumps(received[-1])
        assert "SELECTED_NAUTICAL_LOCALE" in body and "KEEP_ORIGINAL_LEDGER_IDS" in body
        assert "UNSELECTED_ORIGINAL_WINDOW_CANARY" not in body
        assert received[-1].get("previous_response_id") is None
        with store.pool.connection() as c:
            binding=c.execute("SELECT * FROM hb_purpose_context WHERE agent=%s AND purpose='writer'",(peer,)).fetchone()
            assert str(binding["context_id"])==contexts["writer"] and binding["native_thread"]==r.thread
            assert binding["rebuild_generation"]==1 and binding["seed_review"] is None and binding["rebuild_requested"] is None
            delivery=c.execute("SELECT state,generation,event FROM hb_delivery WHERE event=%s AND agent=%s",
                              (incoming["event"]["id"],peer)).fetchone()
            assert delivery["state"]=="pending" and delivery["generation"]==0
            assert c.execute("SELECT count(*) AS n FROM hb_context_selection WHERE agent=%s",(peer,)).fetchone()["n"]==0
        checks.append("Only explicit authenticated human approval admits native apply; original receipt/generation verified, next wire input retains pins and omits old canary without rewriting model selections")

        for label,change_source in (("cancel",False),("changed-source",True),("revoked-reviewer",False),("after-admission",False)):
            path,summary=await draft(pin=change_source)
            before=history.read_bytes()
            await bridge.advance(summary)
            status=await call(owner,"GET",path+"/control")
            if change_source:
                with store.pool.connection() as c:
                    c.execute("UPDATE hb_context_material SET value=value || %s,revision=revision+1 WHERE id=%s",
                              (Jsonb({"fact":"CHANGED_AFTER_REVIEW"}),material))
                await call(owner,"POST",path+"/control",{"operationId":"stale-approve",
                  "action":"approve","revision":status["control"]["revision"],
                  "approvalDigest":status["control"]["approvalDigest"]},status=409)
            if label=="revoked-reviewer":
                approved=await call(owner,"POST",path+"/control",{"operationId":"approve-before-revoke",
                  "action":"approve","revision":status["control"]["revision"],
                  "approvalDigest":status["control"]["approvalDigest"]})
                with store.pool.connection() as c:
                    c.execute("UPDATE hb_token SET revoked=true WHERE digest=%s",(digest(owner),))
                try:await bridge.advance(approved)
                except AssertionError as error:assert "403" in str(error),str(error)
                else:raise AssertionError("Revoked reviewer admitted dispatch")
                assert history.read_bytes()==before
                with store.pool.connection() as c:
                    c.execute("UPDATE hb_token SET revoked=false WHERE digest=%s",(digest(owner),))
                status=await call(owner,"GET",path+"/control")
            if label=="after-admission":
                approved=await call(owner,"POST",path+"/control",{"operationId":"approve-before-change",
                  "action":"approve","revision":status["control"]["revision"],
                  "approvalDigest":status["control"]["approvalDigest"]})
                fault["mutateAfterAdmission"] = True
                try: await bridge.advance(approved)
                except RuntimeError as error: assert "configuration changed" in str(error)
                else: raise AssertionError("Changed configuration dispatched native replacement")
                fault["mutateAfterAdmission"] = False
                r.config.pop("changedAfterAdmission")
                status=await call(owner,"GET",path+"/control")
                assert status["control"]["state"]=="refused" and history.read_bytes()==before
            cancel=await call(owner,"POST",path+"/control",{"operationId":"cancel-original",
              "action":"cancel","revision":status["control"]["revision"]})
            assert cancel["control"]["state"]=="cancel_requested"
            await purpose("writer","reserve",{"materialIds":[material]},operation=label+"-still-held",status=409)
            await bridge.advance(cancel)
            done=await call(owner,"GET",path+"/control")
            assert done["control"]["state"]=="cancelled"
            assert history.read_bytes()==before and len(received)==2
        path,summary=await draft()
        before=history.read_bytes()
        fault["cancelPath"] = path
        try: await bridge.advance(summary)
        except AssertionError as error: assert "409" in str(error),str(error)
        else: raise AssertionError("Cancelled snapshot preparation was admitted")
        assert not r.failed and r.automatic.idle.pending["state"]=="review_refused"
        cancelled=await call(owner,"GET",path+"/control")
        assert cancelled["control"]["state"]=="cancel_requested"
        await bridge.advance(cancelled)
        assert (await call(owner,"GET",path+"/control"))["control"]["state"]=="cancelled"
        assert history.read_bytes()==before and len(received)==2
        checks.append("Prepared/cancel-during-preparation and post-admission no-dispatch recovery preserve history; changed source and revoked reviewer refuse before write")
        path,summary=await draft(pin=True)
        unchanged=history.read_bytes()
        await bridge.advance(summary)
        current=await call(owner,"GET",path+"/control")
        with store.pool.connection() as c:
            c.execute("UPDATE hb_context_material SET value=value || %s,revision=revision+1 WHERE id=%s",
                      (Jsonb({"fact":"REVIEWED_REPLACEMENT_FACT"}),material))
        replacement_path,replacement_preview=await draft(pin=True,prepare=False)
        replacement_id=replacement_path.rsplit("/",1)[1]
        replacing=await call(owner,"POST",path+"/control",{"operationId":"revise-without-unblocking",
          "action":"revise","revision":current["control"]["revision"],
          "replacementDraftId":replacement_id,"canonicalSha256":replacement_preview["canonicalContentSha256"]})
        await purpose("writer","reserve",{"materialIds":[material]},operation="revision-still-held",status=409)
        transferred=await bridge.advance(replacing)
        assert transferred["draftId"]==replacement_id and transferred["control"]["state"]=="requested"
        assert (await call(owner,"GET",path+"/control"))["control"]["state"]=="superseded"
        assert (await call(owner,"GET",path+"/control/content"))["historical"] is True
        await purpose("writer","reserve",{"materialIds":[material]},operation="transferred-still-held",status=409)
        assert history.read_bytes()==unchanged and len(received)==2
        await bridge.advance(transferred)
        ready=await call(owner,"GET",replacement_path+"/control")
        content=await call(owner,"GET",replacement_path+"/control/content")
        assert "REVIEWED_REPLACEMENT_FACT" in json.dumps(content)
        approved=await call(owner,"POST",replacement_path+"/control",{"operationId":"approve-revised-seed",
          "action":"approve","revision":ready["control"]["revision"],
          "approvalDigest":ready["control"]["approvalDigest"]})
        await bridge.advance(approved)
        assert (await call(owner,"GET",replacement_path+"/control"))["control"]["generation"]==2
        await turn("Continue with the revised approved seed")
        assert len(received)==3
        body=json.dumps(received[-1])
        assert "REVIEWED_REPLACEMENT_FACT" in body and "KEEP_ORIGINAL_LEDGER_IDS" in body
        assert "SELECTED_NAUTICAL_LOCALE" not in body and "UNSELECTED_ORIGINAL_WINDOW_CANARY" not in body
        assert received[-1].get("previous_response_id") is None
        checks.append("Canonical source edit and replacement draft transfer the admission hold without a runnable gap; second native generation adopts new source and omits prior seed fact")
        disabled=await purpose("writer","bind",{"runtimeProtocol":"lax-purpose-runtime/v3",
          "workScheduling":2,"toolCompletion":1,"nativeActionRetention":1,"manualSeedControl":0},
          operation="explicit-settled-disable",turn="bootstrap")
        assert disabled["manualSeedControl"]==0
        identity=uid();path=base+"/seed-drafts/"+identity
        created=await call(owner,"POST",base+"/seed-drafts",{"id":identity,"operationId":"create-disabled"})
        preview=await call(owner,"GET",path)
        await call(owner,"POST",path+"/control",{"operationId":"cannot-prepare-disabled",
          "action":"prepare","revision":created["draft"]["revision"],
          "canonicalSha256":preview["canonicalContentSha256"]},status=403)
        checks.append("Explicit settled capability disable restores default-off admission; old/unpaired profile cannot begin review")
        with store.pool.connection() as c:
            assert c.execute("SELECT count(*) AS n FROM hb_context_permit WHERE state IN ('reserved','running')").fetchone()["n"]==0
        result={"state":"authenticated_native_review_passed","checks":checks,
          "realPostgres":True,"authenticatedHumanHttp":True,"actualNative":True,
          "providerRequestTypes":[x.get("type") for x in received],"scriptedProviderRequests":len(received),
          "realModel":False,"semanticContinuityProved":False,"activeTurnReview":False,
          "physicalPhone":False,"productionChanged":False,"httpRequests":len(http_receipts),
          "schema":schema,"nativeThread":r.thread,"contextId":contexts["writer"]}
        (out/"result.json").write_text(json.dumps(result,indent=2))
        print(json.dumps(result))
    finally:
        if getattr(r,"rpc",None):await r.rpc.close()
        await session.close()
        for server in reversed(servers):await server.cleanup()
        store.close()
        (out/"provider-requests.json").write_text(json.dumps(received,indent=2))
        (out/"native-events.json").write_text(json.dumps(native_events,indent=2))
        (out/"processes.json").write_text(json.dumps(process_records,indent=2))
        assert all(not Path("/proc/"+str(p["pid"])).exists() or
          Path("/proc/"+str(p["pid"])+"/stat").read_text().rsplit(")",1)[1].split()[19]!=p["ticks"]
          for p in process_records),"Owned native process remains"


if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--database-config",type=Path,required=True)
    p.add_argument("--evidence",type=Path,required=True)
    p.add_argument("--binary",type=Path,required=True)
    asyncio.run(asyncio.wait_for(main(p.parse_args()),150))
