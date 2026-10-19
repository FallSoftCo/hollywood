"""One-slot real PostgreSQL cooperation capacity; all temporary facts roll back.

This exercises actual admission/permit/lifecycle transactions with frozen native
terminal facts. It starts no models and does not establish semantic cooperation.
"""
import argparse
import asyncio
import contextlib
import queue
import threading
import urllib.request
import urllib.error
import datetime
import json
from pathlib import Path
from uuid import uuid4
from hollywood_backbone import admission, context_capacity as capacity
from hollywood_backbone.context_system import SCHEMA, lifecycle, perform, change, item
from hollywood_backbone.context_admission import admit
from hollywood_backbone.store import Store
from hollywood_backbone.server import application
from aiohttp import web


def main(args):
    store=Store(json.loads(args.database_config.read_text()))
    proof={"schema":"lax-one-slot-purpose-capacity-pg/v1","modelsStarted":0,
           "mode":"Real PostgreSQL, frozen native terminal/communication facts, all rolled back"}
    try:
        with store.pool.connection() as c:
            before=counts(c)
            c.execute(SCHEMA)
            parent=store.actor(c,args.agent_id)
            peer=store.actor(c,args.peer_id)
            system=c.execute("SELECT * FROM hb_context_system WHERE agent=%s",(parent["id"],)).fetchone()
            host=str(system["host"])
            assert c.execute("SELECT host FROM hb_context_system WHERE agent=%s",(peer["id"],)).fetchone()["host"]==system["host"]
            owner=c.execute("""SELECT * FROM hb_principal WHERE authority=%s
                AND kind='human' AND administrator AND active LIMIT 1""",(parent["authority"],)).fetchone()
            room=str(c.execute("SELECT home_room FROM hb_agent_role WHERE principal=%s",(parent["id"],)).fetchone()["home_room"])
            controller=str(uuid4())
            c.execute("INSERT INTO hb_principal(id,authority,name,kind) VALUES(%s,%s,%s,'service')",
                      (controller,parent["authority"],"rollback-capacity-"+controller))
            c.execute("""INSERT INTO hb_host_admission
                (host,authority,controller,machine_identity,max_concurrent,reserved_interactive,
                 memory_per_turn,memory_reserve,sample_max_age,connector_max_age)
                VALUES(%s,%s,%s,%s,1,0,1048576,0,300,300) ON CONFLICT(host) DO UPDATE
                SET controller=EXCLUDED.controller,max_concurrent=1,reserved_interactive=0,
                    memory_per_turn=1048576,memory_reserve=0,sample_max_age=300,connector_max_age=300""",
                (host,parent["authority"],controller,"rollback-only-capacity-not-independent-observation"))
            c.execute("""INSERT INTO hb_host_sample(host,cpu_slots,available_memory) VALUES(%s,1,1073741824)
                ON CONFLICT(host) DO UPDATE SET cpu_slots=1,available_memory=1073741824,
                  observed_at=clock_timestamp()""",(host,))
            assert capacity.resources(c,host)["total"]==0

            configs=[json.loads(path.read_text()) for path in args.agent_configs]
            credentials={v["agentId"]:v["tokens"] for v in configs}
            api=RollbackAPI(store,c,credentials)
            api.start()
            try:
                api.call(parent,"reader",{"operationId":"legacy-missing-protocol","action":"pending",
                    "nativeThread":"not-used","nativeTurn":"runtime","value":{}},runtime_protocol=None)
            except urllib.error.HTTPError as error:
                assert error.code==400
            else:
                raise AssertionError("Old runtime bypassed required pre-inference protocol")

            def binding(actor,purpose):
                return c.execute("SELECT * FROM hb_purpose_context WHERE agent=%s AND purpose=%s",
                                 (actor["id"],purpose)).fetchone()
            def params(actor,purpose,action,value,turn="pre-inference",operation=None):
                return {"action":action,"operationId":operation or "rollback:"+str(uuid4()),
                        "nativeThread":binding(actor,purpose)["native_thread"],"nativeTurn":turn,"value":value}
            def request(actor,body):
                return store.post_in_transaction(c,owner,room,"rollback-capacity:"+str(uuid4()),{
                    "kind":"request","audience":"room","recipientId":str(actor["id"]),"body":body})
            def reserve(actor,purpose,ids,operation):
                p=params(actor,purpose,"reserve",{"materialIds":ids},operation=operation)
                return api.call(actor,purpose,p)
            def begin(actor,purpose,ids,permit):
                turn="rollback-native:"+str(uuid4())
                p=params(actor,purpose,"begin",{"materialIds":ids,"permitId":permit["permit"]["id"]},turn)
                result=api.call(actor,purpose,p)
                return turn,result
            def end(actor,purpose,turn,permit):
                return api.call(actor,purpose,
                    params(actor,purpose,"end",{"status":"completed","permitId":permit["permit"]["id"]},turn))
            def accept(actor,event,turn):
                return perform(store,c,actor,"reader",binding(actor,"reader"),
                    params(actor,"reader","accept",{"inputId":event,"objective":"Rollback-only capacity",
                        "acceptance":"No model or business work"},turn))["workId"]
            def claim(actor,work):
                p=params(actor,"writer","admit",{"workId":work,"capabilities":sorted(admission.CAPABILITIES)})
                return admit(store,c,actor,binding(actor,"writer"),p)

            source=request(parent,"Disposable parent work")
            event=source["event"]["id"]
            r=reserve(parent,"reader",[event],"rollback-parent-reader")
            assert r["permit"]["state"]=="reserved",r
            rt,_=begin(parent,"reader",[event],r)
            work=accept(parent,event,rt)
            end(parent,"reader",rt,r)
            assert capacity.resources(c,host)["total"]==0
            parent_claim=claim(parent,work)
            assert parent_claim["delivery"]["state"]=="claimed",parent_claim
            w=reserve(parent,"writer",[work],"rollback-parent-writer")
            wt,scope=begin(parent,"writer",[work],w)
            assert capacity.resources(c,host)["total"]==1

            child=request(peer,"Disposable peer work, frozen communication fixture")
            child_event=child["event"]["id"]
            blocked=reserve(peer,"reader",[child_event],"rollback-peer-reader")
            original=api.call(peer,"reader",None,operation="rollback-peer-reader")
            assert original["permit"]["id"]==blocked["permit"]["id"]
            assert blocked["permit"]["state"]=="queued" and blocked["admission"]["reason"]=="host_capacity_exhausted",blocked
            parent_work=item(c,store,parent,work,"work")
            change(c,parent_work,"waiting",{**parent_work["value"],"fixture":"Known peer prerequisite"})
            # Waiting work and a close request alone do not establish model stop.
            api.call(parent,"writer",params(parent,"writer","close",{},wt))
            still_blocked=reserve(peer,"reader",[child_event],"rollback-peer-reader")
            assert still_blocked["permit"]["state"]=="queued"
            assert capacity.resources(c,host)["total"]==1
            end(parent,"writer",wt,w)
            assert capacity.resources(c,host)["total"]==0
            parent_d=store.claim_receipt(c,parent_claim["delivery"]["id"])["delivery"]
            assert parent_d["state"]=="running" and parent_d["generation"]==1
            assert parent_d["claim_id"]==parent_claim["delivery"]["claim_id"]

            admitted=reserve(peer,"reader",[child_event],"rollback-peer-reader")
            assert admitted["permit"]["id"]==blocked["permit"]["id"]
            assert admitted["permit"]["state"]=="reserved",admitted
            pt,_=begin(peer,"reader",[child_event],admitted)
            peer_work=accept(peer,child_event,pt)
            end(peer,"reader",pt,admitted)
            peer_claim=claim(peer,peer_work)
            assert peer_claim["delivery"]["state"]=="claimed",peer_claim
            pw=reserve(peer,"writer",[peer_work],"rollback-peer-writer")
            pwt,_=begin(peer,"writer",[peer_work],pw)
            assert capacity.resources(c,host)["total"]==1
            end(peer,"writer",pwt,pw)
            assert capacity.resources(c,host)["total"]==0

            # A responder is also accounted for, independently of writer work.
            need=c.execute("SELECT * FROM hb_context_material WHERE agent=%s AND source=%s AND kind='need'",
                           (parent["id"],event)).fetchone()
            change(c,need,"ready",{**need["value"],"material":"Frozen status output, no publication","prerequisites":[]})
            response=reserve(parent,"responder",[str(need["id"])],"rollback-parent-responder")
            assert response["permit"]["state"]=="reserved",response
            st,_=begin(parent,"responder",[str(need["id"])],response)
            assert capacity.resources(c,host)["total"]==1
            end(parent,"responder",st,response)
            assert capacity.resources(c,host)["total"]==0

            # Age does not turn an uncertain submitted original into free capacity.
            later=request(parent,"Disposable original submission-uncertain fixture")
            held=reserve(parent,"reader",[later["event"]["id"]],"rollback-unknown-original")
            assert held["permit"]["state"]=="reserved",held
            c.execute("UPDATE hb_context_permit SET created_at=clock_timestamp()-interval '2 hours' WHERE id=%s",
                      (held["permit"]["id"],))
            queued=reserve(peer,"writer",[peer_work],"rollback-peer-after-unknown")
            assert queued["permit"]["state"]=="queued"
            assert capacity.resources(c,host)["total"]==1
            proof.update({"configuredSlots":1,"purposePermitsExercised":["reader","writer","responder"],
                "parentWaitRetainedOriginalWorkClaim":True,"peerProgressedWithoutExtraSlot":True,
                "closeRequestDidNotReleaseInference":True,"exactTerminalTurnReleasedInference":True,
                "queuedOriginalPermitIdentityPreserved":True,"unconfirmedOriginalStayedHeldDespiteAge":True,
                "parentGeneration":parent_d["generation"],"modelsOrProcessesStarted":0,
                "installedExecutorOrSemanticCooperationProven":False,
                "oldRuntimeDeniedBeforeModelAdmission":True})
            proof["authenticatedHttpCalls"]=api.calls
            api.close()
            c.rollback()
            assert counts(c)==before
            proof["allSyntheticRecordsRolledBack"]=True
    finally:
        if "api" in locals():
            api.close()
        store.close()
    proof["at"]=datetime.datetime.now(datetime.timezone.utc).isoformat()
    args.evidence_output.write_text(json.dumps(proof,indent=2)+"\n")
    print(json.dumps(proof))


class RollbackAPI:
    """Real authenticated HTTP server pinned to one rollback-only PG fixture."""
    def __init__(self,store,connection,credentials):
        self.store,self.connection,self.credentials=store,connection,credentials
        self.original_pool=store.pool
        self.ready=queue.Queue()
        self.calls=0
        self.closed=False

    def start(self):
        fixture=self
        class Pool:
            @contextlib.contextmanager
            def connection(self):
                with fixture.connection.transaction():
                    yield fixture.connection
        self.store.pool=Pool()
        self.thread=threading.Thread(target=self.run,daemon=True)
        self.thread.start()
        value=self.ready.get(timeout=15)
        if isinstance(value,BaseException):
            raise value
        self.url=value

    def run(self):
        async def serve():
            self.loop=asyncio.get_running_loop()
            self.stop=asyncio.Event()
            runner=web.AppRunner(application(self.store))
            await runner.setup()
            try:
                site=web.TCPSite(runner,"127.0.0.1",0)
                await site.start()
                port=site._server.sockets[0].getsockname()[1]
                self.ready.put("http://127.0.0.1:"+str(port)+"/hollywood/v3/")
                await self.stop.wait()
            finally:
                await runner.cleanup()
        try:
            asyncio.run(serve())
        except BaseException as error:
            self.ready.put(error)

    def call(self,actor,purpose,payload,operation=None,runtime_protocol="lax-purpose-runtime/v3"):
        path="agents/"+str(actor["id"])+"/contexts/"+purpose
        if operation:
            path+="/operations/"+operation
        if payload is not None and runtime_protocol is not None:
            payload={**payload,"runtimeProtocol":runtime_protocol}
        self.calls+=1
        req=urllib.request.Request(self.url+path,method="GET" if operation else "POST",
            data=None if operation else json.dumps(payload).encode(),
            headers={"Authorization":"Bearer "+self.credentials[str(actor["id"])][purpose],
                     "Content-Type":"application/json"})
        with urllib.request.urlopen(req,timeout=15) as response:
            result=json.load(response)
        return result

    def close(self):
        if self.closed:
            return
        self.closed=True
        if hasattr(self,"loop") and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.stop.set)
            self.thread.join(timeout=15)
            if self.thread.is_alive():
                raise RuntimeError("Owned rollback API did not drain")
        self.store.pool=self.original_pool


def counts(c):
    return {t:c.execute("SELECT count(*) AS n FROM "+t).fetchone()["n"]
            for t in ("hb_event","hb_delivery","hb_claim","hb_context_material","hb_principal")}


if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--database-config",type=Path,required=True)
    p.add_argument("--agent-id",required=True)
    p.add_argument("--peer-id",required=True)
    p.add_argument("--agent-configs",type=Path,nargs=2,required=True)
    p.add_argument("--evidence-output",type=Path,required=True)
    main(p.parse_args())
