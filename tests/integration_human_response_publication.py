"""Actual HTTP/PostgreSQL response-audience mechanics.

Native execution, persistent context and capacity are disclosed metadata
fixtures. No model, Linux agent, phone, production authority or business acts.
A separate real native multi-context delivery must qualify semantic behavior.
"""
import asyncio
import hashlib
import json
import os
import secrets
import sys
from pathlib import Path
from uuid import uuid4

sys.path[:0] = [str(Path(__file__).parent), str(Path(__file__).parents[1])]
from aiohttp import ClientSession, web
from hollywood_backbone.server import application
from hollywood_backbone.store import Store
from hollywood_backbone.admission import CAPABILITIES as ADMISSION_CAPABILITIES
from fixture_admission import prepare, CAPABILITIES

async def main():
    os.umask(0o077)
    root = Path(os.environ["PUBLICATION_EVIDENCE"])
    root.mkdir(mode=0o700)
    ids = {name: str(uuid4()) for name in ("authority", "globalRoom", "projectRoom", "host", "workspace")}
    credentials = {name: {"principalId": str(uuid4()), "token": secrets.token_urlsafe(40)}
                   for name in ("Owner", "Global", "Human", "Peer")}
    (root/"intent.json").write_text(json.dumps({"ids": ids, "credentials": credentials,
        "scope": "New metadata-fixture authority only, actual HTTP+PG, no models/native workers"}, indent=2))
    store = Store(json.loads(Path(os.environ["PUBLICATION_DATABASE_CONFIG"]).read_text()))
    assert store.pool is not None
    store.bootstrap(ids["authority"], "Declared native human response audience",
        {"globalRoomId": ids["globalRoom"], "credentials": {k: credentials[k] for k in ("Owner","Global")}})
    runner = web.AppRunner(application(store))
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    base = "http://127.0.0.1:" + str(site._server.sockets[0].getsockname()[1]) + "/hollywood/v3/"
    trace, checks, answers = [], {}, []
    try:
        async with ClientSession() as session:
            async def call(who, method, path, payload=None, status=200):
                async with session.request(method, base+path, json=payload,
                    headers={"Authorization": "Bearer "+credentials[who]["token"]}) as r:
                    value = await r.json()
                    trace.append({"who":who,"method":method,"path":path,"status":r.status,"response":value})
                    (root/"http-trace.json").write_text(json.dumps(trace, default=str))
                    assert r.status == status, (path, r.status, value)
                    return value
            for name,kind in [("Human","human"),("Peer","agent")]:
                await call("Owner","POST","principals",{"id":credentials[name]["principalId"],
                    "name":"Fixture "+name,"kind":kind,"enrollmentCredential":credentials[name]["token"]})
            await call("Owner","POST","rooms",{"id":ids["projectRoom"],"name":"Shared policy review",
                "purpose":"Authenticate policy without accessing private attention controls",
                "ownerId":credentials["Global"]["principalId"]})
            revisions = {}
            async def grant(name, room, post, request=False):
                key=(name,room)
                r=await call("Owner","PUT","rooms/"+room+"/membership",
                    {"principalId":credentials[name]["principalId"],"can_read":True,
                     "can_post":post,"can_request":request,"revision":revisions.get(key,0)})
                revisions[key]=r.get("revision", r.get("membership",{}).get("revision"))
                assert isinstance(revisions[key],int), r
            await grant("Human",ids["globalRoom"],True,True)
            await grant("Human",ids["projectRoom"],True)
            await grant("Peer",ids["projectRoom"],False)
            await grant("Peer",ids["globalRoom"],False)
            ag=credentials["Global"]["principalId"]
            await call("Owner","POST","placements",{"hostId":ids["host"],"name":"Synthetic no-model capacity",
                "identityFingerprint":hashlib.sha256(ids["host"].encode()).hexdigest(),
                "capabilities":{"fixtureOnly":True},"agentId":ag})
            prepare(store, credentials["Owner"]["principalId"],ids["host"],root)
            role=await call("Global","GET","agents/"+ag+"/role")
            original=await call("Human","POST","rooms/"+ids["globalRoom"]+"/messages",
                {"operationId":"publication-fixture-original-task","kind":"request","audience":"room",
                 "recipientId":ag,"body":"Disclosed no-model metadata task for question publication"})
            claimed=await call("Global","POST","connector/claim",
                {"operationId":"publication-fixture-original-claim","hostId":ids["host"],
                 "capabilities":sorted({*CAPABILITIES,*ADMISSION_CAPABILITIES,"persistent-agent-context/v1"})})
            d=claimed["delivery"];assert d and d["state"]=="claimed" and d["event"]==original["event"]["id"]
            thread="fixture-declared-response-thread";turn="fixture-"+d["id"]
            await call("Global","POST","connector/deliveries/"+d["id"]+"/agent-binding",
                {"claimId":d["claim_id"],"generation":d["generation"],"nativeThread":thread,
                 "contextId":hashlib.sha256(b"fixture-declared-response-context").hexdigest(),
                 "workspaceId":ids["workspace"],"roleRevision":role["revision"]})
            await call("Global","POST","deliveries/"+d["id"]+"/transition",
                {"operationId":"publication-original-running","claimId":d["claim_id"],
                 "generation":d["generation"],"state":"running","nativeThread":thread,"nativeTurn":turn})
            async def question(name, publication=None, blocking=False, method="hollywood/question", expected=200):
                payload={"id":str(uuid4()),"deliveryId":d["id"],"claimId":d["claim_id"],
                    "generation":d["generation"],"nativeThread":thread,"nativeTurn":turn,
                    "nativeSlot":name,"method":method,"summary":"Choose the shared policy",
                    "request":{"isBlocking":blocking,"questions":[{"id":"policy","header":"Policy",
                        "question":"What regional and total-capacity policy should apply?","options":[]}]}}
                if publication is not None:payload["responsePublication"]=publication
                return await call("Global","POST","attention",payload,expected)
            def response(q, operation):
                return {"operationId":operation,"scope":q["scope"],
                    "response":{"answers":{"policy":{"answers":["EU/US, genuine checks/signatures, 600 seats total."]}}}}
            def event(identity):
                with store.pool.connection() as c:
                    return json.loads(json.dumps(c.execute("SELECT * FROM hb_event WHERE id=%s",(identity,)).fetchone(), default=str))
            def counts(identity):
                with store.pool.connection() as c:
                    e=c.execute("""SELECT count(*) AS n FROM hb_event WHERE authority=%s
                        AND data->>'type'='humanQuestionAnswer' AND data->>'attentionId'=%s""",
                        (ids["authority"],identity)).fetchone()["n"]
                    a=c.execute("SELECT response_event,state FROM hb_attention WHERE id=%s",(identity,)).fetchone()
                    return e,a
            private=await question("private-default")
            assert "responsePublication" not in private["attention"]["request"]
            accepted=await call("Human","POST","attention/"+private["attention"]["id"]+"/response",
                response(private,"private-answer"))
            answers.append(accepted["continuation"]["id"])
            e=event(accepted["attention"]["response_event"])
            checks["private_default_unchanged"]=e["room"]==ids["globalRoom"] and e["kind"]=="request" and e["audience"]=="direct"
            publication={"version":1,"roomId":ids["projectRoom"]}
            public=await question("declared-room",publication)
            request=public["attention"]["request"]
            checks["mandatory_audience_visible_before_answer"]=(
                request["responsePublication"]=={**publication,"roomName":"Shared policy review"}
                and all('Your answer will be shared in room "Shared policy review".' in q["question"] for q in request["questions"]))
            await call("Peer","GET","attention/"+public["attention"]["id"],status=403)
            await call("Global","POST","attention/"+public["attention"]["id"]+"/response",
                response(public,"agent-cannot-be-human"),403)
            assert counts(public["attention"]["id"])[0]==0
            accepted=await call("Human","POST","attention/"+public["attention"]["id"]+"/response",
                response(public,"one-public-human-answer"))
            answers.append(accepted["continuation"]["id"])
            e=event(accepted["attention"]["response_event"])
            checks["one_canonical_human_room_event_original_continuation"]=(
                e["room"]==ids["projectRoom"] and e["kind"]=="message" and e["audience"]=="room"
                and e["author"]==credentials["Human"]["principalId"] and e["recipient"]==ag
                and e["data"]["authorKind"]=="human" and e["data"]["type"]=="humanQuestionAnswer"
                and e["data"]["originalDeliveryId"]==d["id"] and e["source"]==public["attention"]["event"]
                and counts(public["attention"]["id"])[0]==1)
            history=await call("Peer","GET","rooms/"+ids["projectRoom"]+"/history?view=conversation&limit=100")
            visible=next(x for x in history["events"] if x["id"]==str(e["id"]))
            checks["peer_authenticates_shared_human_response_without_private_controls"]=(
                visible["author"]==credentials["Human"]["principalId"] and visible["data"]["authorKind"]=="human"
                and visible["data"]["answers"]==response(public,"unused")["response"]["answers"])
            await call("Peer","GET","attention/"+public["attention"]["id"],status=403)
            original_receipt=await call("Human","GET","attention/"+public["attention"]["id"]+"/commands/one-public-human-answer")
            checks["original_receipt_readback_no_second_post"]=original_receipt==accepted and counts(public["attention"]["id"])[0]==1
            invalid=[("wrong-room",{"version":1,"roomId":str(uuid4())},False,"hollywood/question",403),
                     ("blocking",publication,True,"hollywood/question",400),
                     ("generic-callback",publication,False,"item/tool/requestUserInput",400),
                     ("unknown-version",{"version":2,"roomId":ids["projectRoom"]},False,"hollywood/question",400)]
            with store.pool.connection() as c:
                before=c.execute("SELECT count(*) AS n FROM hb_attention WHERE authority=%s",(ids["authority"],)).fetchone()["n"]
            for name,pub,blocking,method,status in invalid:await question(name,pub,blocking,method,status)
            with store.pool.connection() as c:
                after=c.execute("SELECT count(*) AS n FROM hb_attention WHERE authority=%s",(ids["authority"],)).fetchone()["n"]
            checks["invalid_publication_creates_no_partial_question"]=before==after
            revoked=await question("current-target-rights",publication)
            await grant("Human",ids["projectRoom"],False)
            current=await call("Human","GET","attention/"+revoked["attention"]["id"])
            assert not current["canRespond"]
            await call("Human","POST","attention/"+revoked["attention"]["id"]+"/response",
                response(revoked,"denied-current-target-post"),403)
            n,row=counts(revoked["attention"]["id"])
            checks["current_target_permission_rechecked_atomically"]=n==0 and row["state"]=="pending" and row["response_event"] is None
            await grant("Human",ids["projectRoom"],True)
            current=await call("Human","GET","attention/"+revoked["attention"]["id"])
            assert current["canRespond"]
            corrected=await call("Human","POST","attention/"+revoked["attention"]["id"]+"/response",
                response(current,"one-corrected-authorized-answer"))
            answers.append(corrected["continuation"]["id"])
            checks["known_rejected_scope_can_be_legitimately_corrected"]=counts(revoked["attention"]["id"])[0]==1
            cancelled=await question("cancelled-before-answer",publication)
            await call("Human","POST","deliveries/"+d["id"]+"/cancel",{})
            await call("Human","POST","attention/"+cancelled["attention"]["id"]+"/response",
                response(cancelled,"cancelled-cannot-publish"),409)
            checks["cancelled_original_scope_cannot_publish"]=counts(cancelled["attention"]["id"])[0]==0
            for identity in answers:await call("Owner","POST","deliveries/"+identity+"/cancel",{})
            await call("Global","POST","deliveries/"+d["id"]+"/transition",
                {"operationId":"fixture-original-cancel-ack","claimId":d["claim_id"],
                 "generation":d["generation"],"state":"cancelled","nativeThread":thread,
                 "nativeTurn":turn,"body":"No-model fixture cancellation acknowledged"})
            checks["committed_human_communication_not_rewound_by_cancellation"]=counts(public["attention"]["id"])[0]==1
            result={"passed":all(checks.values()),"checks":checks,"authority":ids["authority"],
                "httpRequests":len(trace),"modelCalls":0,"linuxAgentsStarted":0,
                "scope":"Actual HTTP/PostgreSQL. Native process/context/capacity facts are explicit metadata fixtures. Not semantic, phone, native continuation execution or production proof."}
            (root/"result.json").write_text(json.dumps(result,indent=2)+"\n")
            print(json.dumps(result));assert result["passed"]
    finally:
        await runner.cleanup()
        store.close()

if __name__=="__main__":
    asyncio.run(main())
