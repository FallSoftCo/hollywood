"""Actual PG/HTTP projections and supervision; metadata fixtures are not model calls."""
import asyncio
import hashlib
import json
import os
import secrets
import time
from pathlib import Path
from uuid import uuid4

from aiohttp import ClientSession, web
from hollywood_backbone.server import application
from hollywood_backbone.store import Store, digest, json_value
from fixture_admission import prepare
from lax_connector.connector import CAPABILITIES

async def main():
    os.umask(0o077)
    root = Path(os.environ["FEED_EVIDENCE"])
    root.mkdir(mode=0o700)
    database = json.loads(Path(os.environ["FEED_DATABASE_CONFIG"]).read_text())
    ids = {k: str(uuid4()) for k in ("authority","globalRoom","room","otherRoom","host","A","B","Reporter","Reviewer","Viewer")}
    credentials = {k: {"principalId":str(uuid4()),"token":secrets.token_urlsafe(40)} for k in ("Owner","Global")}
    tokens = {k:v["token"] for k,v in credentials.items()}
    tokens.update({k:secrets.token_urlsafe(40) for k in ("A","B","Reporter","Reviewer","Viewer")})
    intent = {"ids":ids,"credentials":credentials,"tokens":tokens,"fixtureOnly":True}
    (root/"intent.json").write_text(json.dumps(intent))
    store = Store(database)
    store.migrate()
    store.bootstrap(ids["authority"],"Room projection qualification",{"globalRoomId":ids["globalRoom"],"credentials":credentials})
    owner, glob = (credentials[k]["principalId"] for k in ("Owner","Global"))
    runner = None
    checks = []
    trace = (root/"http-trace.jsonl").open("a")

    async def start():
        nonlocal runner
        runner = web.AppRunner(application(store))
        await runner.setup()
        site = web.TCPSite(runner,"127.0.0.1",0)
        await site.start()
        return f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/hollywood/v3/"

    base = await start()
    async with ClientSession() as session:
        async def call(who, method, path, payload=None, status=200):
            async with session.request(method,base+path,json=payload,headers={"Authorization":"Bearer "+tokens[who]}) as r:
                value = await r.json()
                trace.write(json.dumps({"who":who,"method":method,"path":path,"status":r.status,"result":value},default=str)+"\n")
                trace.flush()
                assert r.status == status, (path,r.status,value)
                return value

        async def request(who,room,audience="room"):
            receipt = store.post(owner,room,"request-"+str(uuid4()),{"kind":"request","body":"Disposable metadata fixture; no model execution.","audience":audience,"recipientId":ids.get(who,glob)})
            claim = await call(who,"POST","connector/claim",{"operationId":"claim-"+str(uuid4()),"hostId":ids["host"],"capabilities":CAPABILITIES})
            assert claim["request"]["id"] == receipt["event"]["id"]
            return claim

        async def terminal(who,claim):
            d=claim["delivery"]
            payload={"operationId":"terminal-"+d["id"],"claimId":d["claim_id"],"generation":d["generation"],"state":"completed","nativeThread":"fixture-thread-"+d["agent"],"nativeTurn":"fixture-turn-"+d["id"],"body":"Metadata fixture completed without running a model."}
            await call(who,"POST",f"deliveries/{d['id']}/transition",{**payload,"operationId":"running-"+d["id"],"state":"running"})
            return await call(who,"POST",f"deliveries/{d['id']}/transition",payload)

        async def action(who,claim,item,phase):
            d=claim["delivery"]
            payload={"claimId":d["claim_id"],"generation":d["generation"],"nativeThread":"fixture-thread-"+d["agent"],"nativeTurn":"fixture-turn-"+d["id"],"itemId":item,"phase":phase,"nativeItemDigest":hashlib.sha256((item+phase).encode()).hexdigest(),"item":{"nativeType":"commandExecution","title":"Command","status":"inProgress" if phase=="started" else "completed","command":"printf fixture","cwd":"/fixture"}}
            return await call(who,"POST",f"connector/deliveries/{d['id']}/activity",payload)

        async def page(view,who="Owner",**query):
            from urllib.parse import urlencode
            return await call(who,"GET",f"rooms/{ids['room']}/history?"+urlencode({"view":view,**query}))

        async def all_pages(view,who="Owner"):
            cursor=0; found=[]
            for _ in range(20):
                p=await page(view,who,after=cursor,limit=100)
                assert p["view"]==view
                found.extend(p["events"])
                if p["nextCursor"]==cursor or len(p["events"])<100:return found,p["nextCursor"]
                cursor=p["nextCursor"]
            raise AssertionError("bounded page drain failed")

        async def sse_page(response):
            lines=[]
            while True:
                line=await asyncio.wait_for(response.content.readline(),5)
                assert line,lines
                if line==b"\n":
                    if any(l.startswith("data: ") for l in lines):
                        return json.loads(next(l[6:] for l in lines if l.startswith("data: ")))
                    lines=[]
                else:lines.append(line.decode().rstrip("\n"))

        try:
            for who in ("A","B","Reporter","Reviewer","Viewer"):
                store.principal(owner,ids[who],who,"human" if who=="Viewer" else "agent",tokens[who])
            store.create_room(owner,ids["room"],"Engineering","Disposable concurrent activity",ids["A"])
            store.create_room(owner,ids["otherRoom"],"Private other room","No cross-room copying",ids["B"])
            for who in ("A","B","Reporter","Reviewer","Viewer"):
                store.membership(owner,ids["room"],ids[who],{"can_read":True,"can_post":who!="Viewer","can_request":who!="Viewer"},0)
            store.membership(owner,ids["otherRoom"],ids["B"],{"can_read":True,"can_post":True,"can_request":True},0)
            store.membership(owner,ids["globalRoom"],ids["Reviewer"],{"can_read":True,"can_post":True,"can_request":True},0)
            for agent in [ids[x] for x in ("A","B","Reporter","Reviewer")]+[glob]:
                store.placement(owner,ids["host"],"Fixture host",hashlib.sha256(ids["host"].encode()).hexdigest(),{"fixtureOnly":True},agent)
            prepare(store,owner,ids["host"],root)
            claims={who:await request(who,ids["room"]) for who in ("A","B")}
            started=time.monotonic()
            for wave in range(4):
                jobs=[]
                for who in ("A","B"):
                    for n in range(wave*24,(wave+1)*24):
                        # Deliberately identical native item IDs across agents.
                        jobs.append(action(who,claims[who],"item-"+str(n),"started"))
                await asyncio.gather(*jobs)
                jobs=[action(who,claims[who],"item-"+str(n),"completed") for who in ("A","B") for n in range(wave*24,(wave+1)*24)]
                await asyncio.gather(*jobs)
                if wave==0:
                    await call("Owner","POST",f"rooms/{ids['room']}/messages",{"operationId":"human-during-burst","kind":"message","body":"Actual human-principal conversation fixture during native activity.","audience":"room"})
            burst_seconds=time.monotonic()-started
            events,cursor=await all_pages("activity")
            assert len(events)==384 and len({e["id"] for e in events})==384
            groups={}
            for e in events:groups.setdefault(e["data"]["actionId"],[]).append(e)
            assert len(groups)==192 and all({e["data"]["phase"] for e in es}=={"started","completed"} for es in groups.values())
            assert {e["data"]["agentId"] for e in events}=={ids["A"],ids["B"]}
            assert all(e["data"]["authorKind"]=="agent" and e["data"]["activityVersion"]==1 and e["kind"]=="progress" for e in events)
            chat,_=await all_pages("conversation")
            assert len(chat)==3 and not any(e["data"].get("type")=="executionActivity" for e in chat)
            assert {e["data"]["authorKind"] for e in chat}=={"human"}
            checks.append("384 interleaved lifecycle records from two agents reduce to 192 independent actions; conversation remains three original human-principal records")
            latest=await page("activity",before=9007199254740991,limit=37)
            older=await page("activity",before=latest["olderCursor"],limit=37)
            assert len(latest["events"])==len(older["events"])==37
            assert not {e["id"] for e in latest["events"]}&{e["id"] for e in older["events"]}
            async with session.get(base+f"rooms/{ids['room']}/events?view=activity&batch=1",headers={"Authorization":"Bearer "+tokens["Owner"]}) as r:
                batch1=await sse_page(r);batch2=await sse_page(r)
                assert len(batch1["events"])==len(batch2["events"])==100
            async with session.get(base+f"rooms/{ids['room']}/events?view=activity&batch=1",headers={"Authorization":"Bearer "+tokens["Owner"],"Last-Event-ID":str(batch2["nextCursor"])}) as r:
                resumed=await sse_page(r)
                assert resumed["events"][0]["id"]==events[200]["id"]
            await call("Owner","GET",f"rooms/{ids['room']}/history?view=invalid",status=400)
            await call("Owner","GET",f"rooms/{ids['room']}/events?view=activity&batch=invalid",status=400)
            checks.append("Independent older/forward pages and bounded SSE batches resume at the original cursor without duplicate actions")
            for who in ("A","B"):await terminal(who,claims[who])
            private=await request("B",ids["room"],"direct")
            await action("B",private,"private-item","completed")
            await terminal("B",private)
            public_events,_=await all_pages("activity","Viewer")
            owner_events,_=await all_pages("activity")
            assert len(public_events)==384 and len(owner_events)==385
            await call("Viewer","GET",f"rooms/{ids['otherRoom']}/history?view=activity",status=403)
            other=await request("B",ids["otherRoom"])
            await action("B",other,"other-room-item","completed")
            await terminal("B",other)
            assert len((await all_pages("activity"))[0])==385
            checks.append("Private execution retains original direct audience; another room's activity never leaks through membership in the current room")

            async def missing_work(manager,name):
                aid=glob if manager=="Global" else ids[manager]
                source=store.post(owner,ids["globalRoom"],"source-"+name,{"kind":"request","body":"Metadata fixture for unfinished work visibility.","audience":"room","recipientId":aid})
                gid,wid=str(uuid4()),str(uuid4())
                store.workflow(aid,"goal.create",ids["globalRoom"],"goal-"+name,{"id":gid,"sourceEventId":source["event"]["id"],"title":"Unreported fixture "+name,"objective":"Report original artifact evidence","acceptance":"Explicit independently accepted evidence","reviewBudget":1})
                state=store.goal_read(aid,gid)["goal"]
                store.workflow(aid,"goal.work",gid,"work-"+name,{"revision":state["revision"],"id":wid,"roomId":ids["room"],"ownerId":ids["Reporter"],"specification":"Metadata fixture only; do not run commands","acceptance":"A report is required","dependencies":[]})
                state=store.goal_read(aid,gid)["goal"]
                if state["state"] != "active":
                    store.workflow(aid,"goal.state",gid,"activate-"+name,{"revision":state["revision"],"state":"active","note":"Fixture activation"})
                work=store.work_read(aid,wid)["work"]
                store.workflow(aid,"work.assign",wid,"assign-"+name,{"revision":work["revision"]})
                d=await call("Reporter","POST","connector/claim",{"operationId":"work-claim-"+name,"hostId":ids["host"],"capabilities":CAPABILITIES})
                assert d["request"]["data"]["workId"]==wid
                await terminal("Reporter",d)
                return gid,wid
            store.portfolio_policy_set(owner,glob,"enable-portfolio",{"roomId":ids["globalRoom"],"revision":0,"enabled":True,"reviewsPerWindow":8,"windowSeconds":3600})
            gid,wid=await missing_work("Global","portfolio")
            review=await call("Global","POST","connector/supervision",{"hostId":ids["host"],"capabilities":CAPABILITIES})
            assert len(review["admitted"])==1 and review["admitted"][0]["reference"]==gid
            again=await call("Global","POST","connector/supervision",{"hostId":ids["host"],"capabilities":CAPABILITIES})
            assert again["admitted"]==[]
            observed=store.work_read(owner,wid)
            assert observed["work"]["state"]=="assigned" and observed["execution"]["state"]=="completed"
            # The visibility correction cannot fabricate acceptance or a worker report.
            try:store.workflow(glob,"work.review",wid,"illegal-accept",{"revision":observed["work"]["revision"],"decision":"accepted","note":"Must be denied without submitted evidence"})
            except Exception as error:
                from hollywood_backbone.store import Conflict
                assert isinstance(error,Conflict)
            else:raise AssertionError("unreported work accepted")
            gid2,wid2=await missing_work("Reviewer","designated")
            review2=await call("Reviewer","POST","connector/reviews",{"hostId":ids["host"],"capabilities":CAPABILITIES})
            assert len(review2["admitted"])==1
            assert (await call("Reviewer","POST","connector/reviews",{"hostId":ids["host"],"capabilities":CAPABILITIES}))["admitted"]==[]
            checks.append("Completed but unreported assignments surface once for enabled Global and designated reviewer; original work stays unfinished and cannot be accepted without a report")

            profile=Path(os.environ["FEED_EXISTING_PROFILE"]) if os.environ.get("FEED_EXISTING_PROFILE") else None
            if profile is not None:
                profile=json.loads(profile.read_text())
                tokens["ProductionSource"]=json.loads(Path(profile["credentialFile"]).read_text())["token"]
                existing_room=str(__import__("uuid").UUID(os.environ["FEED_EXISTING_ROOM"]))
                actual=await call("ProductionSource","GET",f"rooms/{existing_room}/history?view=activity&before=9007199254740991&limit=100")
                assert actual["events"] and all(e["data"]["type"]=="executionActivity" and e["data"]["actionId"] for e in actual["events"])
                checks.append("Actual retained production native execution records project with stable action IDs; no new provider turn or production mutation")
            result={"passed":True,"authorityId":ids["authority"],"checks":checks,"burstSeconds":burst_seconds,"lifecycleRecords":384,"actions":192,"nativeModelCalls":0,"fixtureAdmissionsAreNotNativeProof":True,"productionWrites":0}
            (root/"result.json").write_text(json.dumps(result,indent=2))
            print(json.dumps(result))
        finally:
            await runner.cleanup()
            trace.close()
            store.close()

if __name__=="__main__":asyncio.run(main())
