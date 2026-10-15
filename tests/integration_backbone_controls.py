"""Real PostgreSQL/authenticated HTTP controls; native pointers here are explicit fixtures."""
import asyncio,json,os,secrets
from pathlib import Path
from uuid import uuid4
from aiohttp import ClientSession,web
from hollywood_backbone.bootstrap import initialize
from hollywood_backbone.server import application
from hollywood_backbone.store import Store
from lax_connector.host import enroll,supervise

CAPS=["native-role-goals/v1","cooperation-plan/v1","work-attempts/v1","room-briefing/v1","host-admission/v1","portfolio-supervision/v1","native-human-controls/v1"]

async def main():
    os.umask(0o077)
    root=Path(os.environ["CONTROL_EVIDENCE"]);root.mkdir(mode=0o700)
    db=json.loads(Path(os.environ["CONTROL_DATABASE_CONFIG"]).read_text())
    initialize(db,root/"enterprise","Human control HTTP qualification")
    setup=json.loads((root/"enterprise/installation-intent.json").read_text())
    people=setup["credentials"];owner=people["Owner"];glob=people["Global"];room=setup["globalRoomId"];host=str(uuid4())
    store=Store(db);runner=None;trace=[];checks=[]
    async def start():
        nonlocal runner
        runner=web.AppRunner(application(store));await runner.setup()
        await web.TCPSite(runner,"127.0.0.1",18772).start()
    await start()
    async with ClientSession() as session:
        async def call(who,method,path,body=None,status=200):
            async with session.request(method,"http://127.0.0.1:18772/hollywood/v3/"+path,json=body,
                headers={"Authorization":"Bearer "+people[who]["token"]}) as response:
                value=await response.json();trace.append({"who":who,"method":method,"path":path,"status":response.status})
                (root/"trace.json").write_text(json.dumps(trace))
                assert response.status==status,(who,path,response.status,value,status)
                return value
        async def get(who,path,status=200):return await call(who,"GET",path,status=status)
        async def post(who,path,body,status=200):return await call(who,"POST",path,body,status)
        async def grant(name,request,rev):
            return await call("Owner","PUT","rooms/"+room+"/membership",{"principalId":people[name]["principalId"],"can_read":True,"can_post":True,"can_request":request,"revision":rev})
        async def create_execution(who="Owner",audience="room"):
            event=await post(who,"rooms/"+room+"/messages",{"operationId":str(uuid4()),"kind":"request","body":"Explicit metadata-only binding fixture; no native runtime exists.","recipientId":glob["principalId"],"audience":audience})
            claim=await post("Global","connector/claim",{"hostId":host,"operationId":str(uuid4()),"capabilities":CAPS})
            d=claim["delivery"]
            assert d["event"]==event["event"]["id"]
            await post("Global","deliveries/"+d["id"]+"/transition",{"claimId":d["claim_id"],"generation":d["generation"],"operationId":str(uuid4()),"state":"running","nativeThread":"metadata-fixture-thread-"+d["id"],"nativeTurn":"metadata-fixture-turn-"+d["id"]})
            detail=await get(who,"rooms/"+room)
            return d,next(x["control_scope"] for x in detail["deliveries"] if x["id"]==d["id"])
        async def guidance(scope,who="Owner",status=200):
            body={"id":str(uuid4()),"operationId":str(uuid4()),"scope":scope,"body":"Qualified explicit human guidance"}
            result=await post(who,"deliveries/"+scope["deliveryId"]+"/guidance",body,status)
            return body,result
        async def update(d,row,state,evidence=None):
            return await post("Global","connector/native-controls/"+row["control"]["id"],{"operationId":str(uuid4()),"claimId":d["claim_id"],"generation":d["generation"],"scope":row["scope"],"state":state,"evidence":evidence or {}})
        async def finish(d):
            return await post("Global","deliveries/"+d["id"]+"/transition",{"claimId":d["claim_id"],"generation":d["generation"],"operationId":str(uuid4()),"state":"failed","body":"Verified metadata-only cleanup; no native execution was submitted."})
        try:
            for name in ["Other","Peer"]:
                people[name]={"principalId":str(uuid4()),"token":secrets.token_urlsafe(40)}
                await post("Owner","principals",{"id":people[name]["principalId"],"name":name,"kind":"human" if name=="Other" else "agent","enrollmentCredential":people[name]["token"]})
                await grant(name,True,0)
            await post("Owner","placements",{"hostId":host,"name":"Human guidance HTTP","identityFingerprint":"metadata-only-"+setup["authorityId"],"capabilities":{},"agentId":glob["principalId"]})
            enrolled=await asyncio.to_thread(enroll,{"authorityId":setup["authorityId"],"hostId":host,"hollywoodUrl":"http://127.0.0.1:18772","administratorCredential":owner,"policy":{"maxConcurrent":2,"reservedInteractive":1,"controlAgentIds":[glob["principalId"]],"memoryPerTurnBytes":1024**3,"reserveMemoryBytes":1024**3,"sampleMaxAgeSeconds":300,"connectorMaxAgeSeconds":300}})
            await asyncio.to_thread(supervise,json.loads(Path(enrolled["configPath"]).read_text()),True)
            d,scope=await create_execution()
            for who in ["Other","Peer"]:
                await guidance(scope,who,403)
            checks.append("Different full-rights human and peer cannot steer the causal human work")
            for field in ["hostId","generation","nativeTurn","audience","roomId","sourceRequestId"]:
                wrong={**scope,field:scope[field]+1 if field=="generation" else "direct" if field=="audience" else "stale-turn" if field=="nativeTurn" else str(uuid4())}
                await guidance(wrong,status=409)
            checks.append("Host/generation/turn/audience/room/source substitutions denied")
            body,row=await guidance(scope)
            same=await post("Owner","deliveries/"+d["id"]+"/guidance",body)
            assert same["control"]["id"]==row["control"]["id"]
            await post("Owner","deliveries/"+d["id"]+"/guidance",{**body,"body":"Changed"},409)
            checks.append("Stable original control identity; altered contents denied")
            before=(await get("Owner","rooms/"+room))["deliveries"]
            assert len(before)==1
            checks.append("Guidance created no new delivery")
            row=await update(d,row,"dispatching")
            row=await update(d,row,"unknown",{"reason":"explicit-transport-fault-fixture"})
            await runner.cleanup();await start()
            restored=await get("Owner","native-controls/"+body["id"])
            assert restored["control"]["state"]=="unknown"
            assert not restored["nativeAdmissionProved"] and not restored["modelConsumptionProved"]
            checks.append("API restart retains original unknown without admission/consumption claim")
            pending_body,pending_row=await guidance(scope)
            await finish(d)
            assert (await get("Owner","native-controls/"+pending_body["id"]))["control"]["state"]=="rejected"
            assert (await get("Owner","native-controls/"+body["id"]))["control"]["state"]=="unknown"
            checks.append("Terminal original rejects undispatched input; uncertain native intent remains unknown")
            await guidance(scope,status=409)
            d,scope=await create_execution("Other","direct")
            body,row=await guidance(scope,"Other")
            metadata=await get("Owner","rooms/"+room)
            rev=next(m["revision"] for m in metadata["memberships"] if m["principal"]==people["Other"]["principalId"])
            await grant("Other",False,rev)
            denied=await update(d,row,"dispatching")
            assert denied["control"]["state"]=="rejected"
            await get("Peer","native-controls/"+body["id"],403)
            checks.append("Current request permission rechecked before dispatch; private control stays private")
            await finish(d)
            result={"passed":True,"checks":checks,"nativePointers":"Synthetic metadata fixtures only; real native qualification separate","nativeModelsStarted":0,"productionChanged":False,"physicalPhoneChanged":False}
            (root/"result.json").write_text(json.dumps(result,indent=2))
            print(json.dumps(result))
        finally:
            await runner.cleanup();store.close()

if __name__=="__main__":asyncio.run(main())
