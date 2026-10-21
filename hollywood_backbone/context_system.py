"""Purpose-bound native agent communication; no model or execution in Hollywood."""
import json
from uuid import UUID, uuid5
from psycopg.types.json import Jsonb
from .store import Conflict, Denied, digest, json_value
from . import context_capacity

PURPOSES = {"reader", "writer", "responder"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS hb_context_system(
 agent uuid PRIMARY KEY REFERENCES hb_principal(id), authority uuid NOT NULL,
 host uuid NOT NULL REFERENCES hb_host(id), workspace text NOT NULL,
 role_revision bigint NOT NULL, enabled boolean NOT NULL DEFAULT true);
CREATE TABLE IF NOT EXISTS hb_purpose_context(
 agent uuid NOT NULL REFERENCES hb_context_system(agent), purpose text NOT NULL,
 context_id uuid NOT NULL UNIQUE, token_digest text NOT NULL UNIQUE,
 native_thread text, reads jsonb NOT NULL DEFAULT '{}', PRIMARY KEY(agent,purpose),
 CHECK(purpose IN ('reader','writer','responder')));
ALTER TABLE hb_purpose_context ADD COLUMN IF NOT EXISTS active_turn text;
ALTER TABLE hb_purpose_context ADD COLUMN IF NOT EXISTS active_material uuid[] NOT NULL DEFAULT '{}';
ALTER TABLE hb_purpose_context ADD COLUMN IF NOT EXISTS accepting boolean NOT NULL DEFAULT false;
ALTER TABLE hb_purpose_context ADD COLUMN IF NOT EXISTS work_scheduling smallint NOT NULL DEFAULT 1 CHECK(work_scheduling IN (1,2));
CREATE TABLE IF NOT EXISTS hb_context_material(
 id uuid PRIMARY KEY, agent uuid NOT NULL REFERENCES hb_context_system(agent),
 kind text NOT NULL, source uuid NOT NULL REFERENCES hb_event(id),
 state text NOT NULL, value jsonb NOT NULL, revision bigint NOT NULL DEFAULT 1,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp());
CREATE INDEX IF NOT EXISTS hb_context_material_agent ON hb_context_material(agent,kind,state);
CREATE TABLE IF NOT EXISTS hb_context_operation(
 agent uuid NOT NULL, purpose text NOT NULL, operation text NOT NULL,
 digest text NOT NULL, result jsonb NOT NULL, PRIMARY KEY(agent,purpose,operation));
""" + context_capacity.SCHEMA + """\nCREATE TABLE IF NOT EXISTS hb_context_observation_cursor(\n agent uuid PRIMARY KEY REFERENCES hb_context_system(agent), seq bigint NOT NULL CHECK(seq>=0));\n"""

def configure(store, actor_id, agent, payload, *, command=None):
    from .administrative import mutate
    return mutate(store,actor_id,"context_configure",[agent,payload],command)


def configure_transaction(store,c,actor,agent,payload):
    target = c.execute("""SELECT p.*,r.revision FROM hb_principal p
      JOIN hb_agent_role r ON r.principal=p.id
      JOIN hb_placement l ON l.agent=p.id AND l.enabled
      WHERE p.id=%s AND p.authority=%s AND p.kind='agent' AND p.active AND l.host=%s""",
      (agent, actor["authority"], payload["hostId"])).fetchone()
    if not target:
        raise Denied("Enrolled agent placement required")
    c.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("context:"+agent,))
    old = c.execute("SELECT * FROM hb_context_system WHERE agent=%s", (agent,)).fetchone()
    if old:
        existing = configuration(store, str(actor["id"]), agent, connection=c)
        expected = {b["purpose"]: (b["contextId"], b["credentialDigest"]) for b in payload["bindings"]}
        actual = {b["purpose"]: (b["contextId"], b["credentialDigest"]) for b in existing["bindings"]}
        if (old["host"] == UUID(payload["hostId"]) and old["workspace"] == payload["workspaceId"]
                and old["enabled"] and actual == expected and len(payload["bindings"]) == 3):
            from .context_input_reconciliation import reconcile
            reconciled=reconcile(c,store,actor,agent,payload.get("reconcilePendingDeliveryIds",[]))
            return {"agentId":agent,"purposes":sorted(PURPOSES),"configured":True,"reconciled":True,
                    "pendingInputReconciliation":reconciled}
        raise Conflict("Existing context system requires explicit migration")
    if payload.get("reconcilePendingDeliveryIds"):
        raise Conflict("Exact pending-input reconciliation requires an existing enrollment")
    bindings = payload["bindings"]
    if {b["purpose"] for b in bindings} != PURPOSES or len(bindings) != 3:
        raise ValueError("Exactly three purpose bindings required")
    c.execute("INSERT INTO hb_context_system(agent,authority,host,workspace,role_revision) VALUES(%s,%s,%s,%s,%s)",
              (agent,target["authority"],payload["hostId"],payload["workspaceId"],target["revision"]))
    for b in bindings:
        c.execute("INSERT INTO hb_token(digest,principal) VALUES(%s,%s)",(b["credentialDigest"],agent))
        c.execute("INSERT INTO hb_purpose_context(agent,purpose,context_id,token_digest) VALUES(%s,%s,%s,%s)",
                  (agent,b["purpose"],b["contextId"],b["credentialDigest"]))
    from .context_room_observations import enroll
    enroll(c,target)
    # Requests accepted before the first process was installed remain canonical.
    # Ingest their original IDs; never re-post or create replacement deliveries.
    pending=c.execute("""SELECT d.event FROM hb_delivery d JOIN hb_event e ON e.id=d.event
        WHERE d.agent=%s AND d.state='pending' ORDER BY e.seq LIMIT 1001""",(agent,)).fetchall()
    if len(pending)>1000:
        raise Conflict("Large historical queue requires an explicit migration")
    for row in pending:
        store.readable_event(c,target,row["event"])
        ingest(c,agent,row["event"])
    return {"agentId":agent,"purposes":sorted(PURPOSES),"configured":True,
            "existingPendingInputsIngested":len(pending)}

def ingest(c, agent, event):
    if not c.execute("SELECT 1 FROM hb_context_system WHERE agent=%s AND enabled",(agent,)).fetchone():
        return
    identity = str(event)
    from .context_peers import incoming
    peer = incoming(c, agent, event)
    source=c.execute("SELECT data FROM hb_event WHERE id=%s",(event,)).fetchone()
    question=source["data"].get("type")=="humanQuestionAnswer"
    materials = [("input", "pending", peer or ({"humanQuestionAnswer":True} if question else {}))]
    if (peer is None or peer.get("responseRequired")) and not question:
        materials.append(("need", "interpreting", {"inputId": identity, "required": True, "prerequisites": []}))
    for kind, state, value in materials:
        key = identity if kind == "input" else str(uuid5(UUID(identity), "response"))
        c.execute("""INSERT INTO hb_context_material(id,agent,kind,source,state,value)
          VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO NOTHING""",
          (key,agent,kind,event,state,Jsonb(value)))

def item(c, store, actor, identity, kind=None):
    row = c.execute("SELECT * FROM hb_context_material WHERE id=%s AND agent=%s FOR UPDATE",
                    (identity,actor["id"])).fetchone()
    if not row or kind and row["kind"] != kind:
        raise Denied("Material unavailable")
    store.readable_event(c,actor,row["source"])
    return row

def change(c, row, state, value):
    c.execute("""UPDATE hb_context_material SET state=%s,value=%s,revision=revision+1,
       updated_at=clock_timestamp() WHERE id=%s""",(state,Jsonb(value),row["id"]))

def need_for(c, store, actor, source):
    return item(c,store,actor,str(uuid5(UUID(str(source)),"response")),"need")

def call(store, actor_id, token_digest, agent, purpose, p):
    with store.pool.connection() as c:
        actor=store.actor(c,actor_id)
        if str(actor["id"])!=agent or actor["kind"]!="agent" or purpose not in PURPOSES:
            raise Denied("Own native context required")
        c.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",("context:"+agent,))
        system=c.execute("""SELECT s.* FROM hb_context_system s JOIN hb_placement l
          ON l.agent=s.agent AND l.host=s.host AND l.enabled
          JOIN hb_agent_role r ON r.principal=s.agent
          WHERE s.agent=%s AND s.enabled""",(agent,)).fetchone()
        binding=c.execute("""SELECT b.* FROM hb_purpose_context b JOIN hb_token t
          ON t.digest=b.token_digest AND NOT t.revoked
          WHERE b.agent=%s AND b.purpose=%s AND b.token_digest=%s FOR UPDATE OF b""",
          (agent,purpose,token_digest)).fetchone()
        if not system or not binding:
            raise Denied("Purpose credential or installed placement unavailable")
        role=c.execute("SELECT revision FROM hb_agent_role WHERE principal=%s",(actor["id"],)).fetchone()
        if system["role_revision"]!=role["revision"]:
            c.execute("UPDATE hb_context_system SET role_revision=%s WHERE agent=%s",
                      (role["revision"],actor["id"]))
        if p["action"] in ("pending", "ready"):
            if p["nativeThread"]!=binding["native_thread"]:
                raise Conflict("Native context binding changed")
            return json_value(perform(store,c,actor,purpose,binding,p))
        op=p["operationId"]
        fingerprint=digest(p)
        old=c.execute("SELECT * FROM hb_context_operation WHERE agent=%s AND purpose=%s AND operation=%s",
                      (agent,purpose,op)).fetchone()
        if old:
            if p["action"]=="bind" and binding["work_scheduling"]==2 and p["value"].get("workScheduling",1)!=2:
                raise Conflict("Explicit drained work-scheduling downgrade required")
            if old["digest"]!=fingerprint:
                raise Conflict("Original context operation changed")
            return old["result"]
        if p["action"]=="bind":
            if p["value"] not in ({"runtimeProtocol":"lax-purpose-runtime/v3"},
                    {"runtimeProtocol":"lax-purpose-runtime/v3","workScheduling":2}):
                raise Conflict("Native work and inference admission runtime required")
            scheduling=p["value"].get("workScheduling",1)
            if binding["work_scheduling"]==2 and scheduling==1:
                raise Conflict("Explicit drained work-scheduling downgrade required")
            c.execute("UPDATE hb_purpose_context SET work_scheduling=%s WHERE agent=%s AND purpose=%s",
                      (scheduling,actor["id"],purpose))
            if binding["native_thread"] and binding["native_thread"]!=p["nativeThread"]:
                raise Conflict("Original context thread changed")
            c.execute("UPDATE hb_purpose_context SET native_thread=%s WHERE agent=%s AND purpose=%s",
                      (p["nativeThread"],agent,purpose))
            result={"contextId":str(binding["context_id"]),"nativeThread":p["nativeThread"],"purpose":purpose,
                    "runtimeProtocol":"lax-purpose-runtime/v3","workUpdates":1,"workScheduling":scheduling,"roleWork":1,"roleDecisionOwner":1}
        elif p["nativeThread"]!=binding["native_thread"]:
            raise Conflict("Native context binding changed")
        else:
            result=perform(store,c,actor,purpose,binding,p)
        if p["action"] == "reserve" and result["permit"]["state"] == "queued":
            return json_value(result)
        if p["action"] == "admit" and result.get("delivery") is None:
            return json_value(result)  # No execution accepted; safe confirmed deferral.
        result=json_value(result)
        c.execute("INSERT INTO hb_context_operation VALUES(%s,%s,%s,%s,%s)",
                  (agent,purpose,op,fingerprint,Jsonb(result)))
        return result

def lifecycle(c, store, actor, purpose, binding, p):
    action, turn = p["action"], p["nativeTurn"]
    if action == "begin":
        if binding["active_turn"] not in (None, turn):
            raise Conflict("Original native turn is unsettled")
        ids = p["value"].get("materialIds", [])
        if purpose == "writer" and len(ids) != 1:
            raise ValueError("One original work owns a writer turn")
        if not ids or len(ids) > 100 or len(set(ids)) != len(ids):
            raise ValueError("A bounded original material set is required")
        context_capacity.begin(c,actor,purpose,binding,p,ids)
        kind = {"reader": "input", "writer": "work", "responder": "need"}[purpose]
        for identity in ids:
            row = item(c, store, actor, identity, kind)
            if row["state"] not in (("pending", "running") if purpose == "writer" else
                                    ("pending",) if purpose == "reader" else ("ready",)):
                raise Conflict("Original material is no longer runnable")
            delivery = c.execute("SELECT cancellation_requested FROM hb_delivery WHERE event=%s AND agent=%s",
                                 (row["source"], actor["id"])).fetchone()
            if delivery and delivery["cancellation_requested"]:
                raise Conflict("Original request cancelled before admission")
            if purpose == "writer":
                change(c, row, "running", {**row["value"], "execution": {
                    "nativeThread": p["nativeThread"], "nativeTurn": turn, "status": "inProgress",
                    "resumeRevision":row["value"].get("resumeRevision",0)}})
        c.execute("UPDATE hb_purpose_context SET active_turn=%s,active_material=%s::uuid[],accepting=true WHERE agent=%s AND purpose=%s",
                  (turn, ids, actor["id"], purpose))
        from .context_execution import capture
        scopes = capture(c, store, actor, purpose, binding, p, ids)
        return {"nativeTurn": turn, "state": "running", "materialIds": ids,
                "executionScopes": scopes}
    if binding["active_turn"] not in (None, turn):
        raise Conflict("Another native turn owns this context")
    if action == "close":
        c.execute("UPDATE hb_purpose_context SET accepting=false WHERE agent=%s AND purpose=%s",
                  (actor["id"], purpose))
        return {"nativeTurn": turn, "state": "callbacks_closed"}
    status = p["value"].get("status")
    if status not in ("completed", "interrupted", "failed"):
        raise ValueError("A confirmed terminal native status is required")
    admission=context_capacity.end(c,actor,purpose,binding,p)
    for identity in binding["active_material"]:
        row = item(c, store, actor, str(identity))
        delivery = c.execute("SELECT cancellation_requested FROM hb_delivery WHERE event=%s AND agent=%s",
                             (row["source"], actor["id"])).fetchone()
        cancelled = delivery and delivery["cancellation_requested"]
        if row["kind"] == "work" and row["state"] not in ("complete", "cancelled"):
            change(c, row, "cancelled" if cancelled else "waiting" if row["state"]=="waiting" else "pending",
                   {**row["value"], "execution": {"nativeThread": p["nativeThread"],
                    "nativeTurn": turn, "status": status,
                    "resumeRevision":row["value"].get("execution",{}).get("resumeRevision",row["value"].get("resumeRevision",0))}})
        if cancelled:
            c.execute("""UPDATE hb_context_material SET state='cancelled',revision=revision+1,
              updated_at=clock_timestamp() WHERE agent=%s AND source=%s
              AND state NOT IN ('published','complete','cancelled','handled')""",
                      (actor["id"], row["source"]))
            c.execute("""UPDATE hb_delivery SET state='cancelled',revision=revision+1,
              updated_at=clock_timestamp() WHERE agent=%s AND event=%s AND cancellation_requested
              AND state NOT IN ('completed','cancelled')""", (actor["id"], row["source"]))
    c.execute("UPDATE hb_purpose_context SET active_turn=NULL,active_material='{}',accepting=false WHERE agent=%s AND purpose=%s",
              (actor["id"], purpose))
    if purpose == "writer":
        from .purpose_terminal_recovery import reconcile
        for identity in binding["active_material"]:
            material = item(c, store, actor, str(identity))
            if material["kind"] == "work":
                original = c.execute("SELECT id FROM hb_delivery WHERE event=%s AND agent=%s",
                                     (material["source"], actor["id"])).fetchone()
                if original:
                    reconcile(store, c, original["id"])
    return {"nativeTurn": turn, "state": status,"admission":admission}


def perform(store,c,actor,purpose,binding,p):
    action,v=p["action"],p["value"]
    if action == "release_reservation":
        return context_capacity.release_unused(c,actor,purpose,binding,p)
    if action == "reserve":
        return context_capacity.reserve(store,c,actor,purpose,binding,p)
    if action in ("admit", "ready"):
        if purpose != "writer":
            raise Denied("Only the writer may claim accepted work")
        from .context_admission import admit, ready
        return (admit if action == "admit" else ready)(store, c, actor, binding, p)
    if action in ("begin", "close", "end"):
        return lifecycle(c, store, actor, purpose, binding, p)
    if action in ("inbox", "inbox_adopt"):
        if purpose != "reader":
            raise Denied("Primary input delivery belongs to the reader")
        from .context_inbox import snapshot, adopt
        if action == "inbox":
            return snapshot(c, store, actor, binding, p)
        if not binding["accepting"] or binding["active_turn"] != p["nativeTurn"]:
            raise Conflict("Original reader turn no longer admits delivery")
        return adopt(c, store, actor, binding, p)
    if action == "activity":
        # Recovery may append this immutable log after the original turn closes.
        # It neither admits callbacks nor changes execution/work settlement.
        from .context_execution import append_activity
        return append_activity(c, store, actor, purpose, binding, p)
    if action not in ("pending",) and (not binding["accepting"] or binding["active_turn"] != p["nativeTurn"]):
        raise Conflict("Original native turn no longer admits callbacks")
    permitted={
      "reader":{"pending","snapshot","read","accept","answer","announce","peer_result","amend","directory","observe","initiative","room_list","room_history"},
      "writer":{"pending","snapshot","read","progress","complete","announce","peer_request","wait_peers","wait_goals","directory","response_material"},
      "responder":{"pending","snapshot","read","publish","suppress"},
    }
    if action not in permitted[purpose]:
        raise Denied("Tool does not belong to this context purpose")
    if action in ("room_list","room_history"):
        from . import context_room_reads
        return (context_room_reads.rooms if action=="room_list" else context_room_reads.history)(c,store,actor,v)
    if action in ("observe", "initiative"):
        if v.get("observationId") not in {str(x) for x in binding["active_material"]}:
            raise Denied("Observation does not belong to this native turn")
        from .context_room_observations import record
        from .context_role_work import initiate
        return (record if action == "observe" else initiate)(c,store,actor,binding,p)
    target=v.get("workId") if action in ("progress","complete","peer_request","wait_peers","wait_goals","response_material") else v.get("inputId") if action in ("accept","answer","peer_result","amend") else v.get("communicationId")
    if target and target not in {str(x) for x in binding["active_material"]}:
        raise Denied("Material does not belong to this captured native turn")
    if action=="wait_goals":
        from .context_goal_dependencies import wait
        return wait(c,store,actor,binding,p)
    if action=="response_material":
        from .context_peer_updates import response_material
        return response_material(c,store,actor,binding,p)
    if action=="amend":
        if purpose!="reader":
            raise Denied("Only the reader interprets work updates")
        from .context_work_updates import apply
        return apply(c,store,actor,binding,p)
    if action in ("directory", "peer_request", "wait_peers", "peer_result"):
        from . import context_peers
        if action == "directory":
            return context_peers.directory(c,store,actor,v)
        function = {"peer_request": context_peers.queue, "wait_peers": context_peers.wait,
                    "peer_result": context_peers.apply}[action]
        return function(c,store,actor,binding,p)
    if action=="pending":
        c.execute("UPDATE hb_purpose_context SET observed_at=clock_timestamp() WHERE agent=%s AND purpose=%s",
                  (actor["id"],purpose))
        if purpose=="reader":
            if v.get("capabilities") is not None:
                from .work import dispatch_reviews
                system=c.execute("SELECT host FROM hb_context_system WHERE agent=%s",(actor["id"],)).fetchone()
                dispatch_reviews(store,str(actor["id"]),str(system["host"]),v["capabilities"],connection=c)
                from .portfolio import dispatch
                dispatch(store,str(actor["id"]),str(system["host"]),v["capabilities"],connection=c)
            from .context_peer_updates import refresh
            refresh(c,store,actor)
            from .context_room_observations import refresh as observations
            observations(c,store,actor)
        if purpose=="writer":
            from .context_goal_dependencies import refresh
            refresh(c,store,actor)
            from .context_cancellations import settle_idle
            settle_idle(c,store,actor)
            if binding["work_scheduling"]==2:
                from .context_work_frontier import normalize
                normalize(c,actor)
        kind={"reader":"input","writer":"work","responder":"need"}[purpose]
        rows=c.execute("""SELECT m.id,m.kind,m.state,m.revision,
          coalesce((m.value->>'resumeRevision')::bigint,0) AS resume_revision,
          COALESCE(d.cancellation_requested,false) AS cancelled
          FROM hb_context_material m LEFT JOIN hb_delivery d
          ON d.event=m.source AND d.agent=m.agent WHERE m.agent=%s AND m.kind=%s
          AND ((m.kind='input' AND m.state='pending')
            OR (m.kind='work' AND m.state IN ('pending','running'))
            OR (m.kind='need' AND m.state='ready'))
          AND (m.kind<>'work' OR %s=1 OR m.id=ANY(%s::uuid[])
               OR COALESCE(d.cancellation_requested,false)
               OR coalesce((m.value->>'resumeRevision')::bigint,0)>
                  coalesce((m.value->'execution'->>'resumeRevision')::bigint,-1))
          ORDER BY m.created_at,m.id LIMIT 100""",
          (actor["id"],kind,binding["work_scheduling"],binding["active_material"])).fetchall()
        return {"pending":json_value(rows)}
    if action in ("snapshot", "read"):
        from .context_material import index, read
        retrieve = index if action == "snapshot" else read
        return retrieve(c,store,actor,purpose,binding,p["nativeTurn"],v)
    if action in ("accept","answer"):
        source=item(c,store,actor,v["inputId"],"input")
        from .context_peers import require_current_read
        require_current_read(binding,source,p["nativeTurn"])
        if source["state"]!="pending":
            raise Conflict("Input already interpreted")
        if source["value"].get("roomObservation"):
            raise Conflict("Observation is not an assigned request or required answer; use record_room_observation")
        if source["value"].get("peerCompletion"):
            raise Conflict("Record the peer result on its original work; no new task or acknowledgement is required")
        if action=="accept" and source["value"].get("peerContinuation"):
            raise Conflict("Apply the causally linked peer follow-up to parentWorkId; do not create a competing task")
        if source["value"].get("humanQuestionAnswer"):
            raise Conflict("Apply the human answer to its original work; do not create a new task or acknowledgement")
        delivery=c.execute("SELECT state,cancellation_requested FROM hb_delivery WHERE event=%s AND agent=%s",
                           (source["source"],actor["id"])).fetchone()
        if not delivery or delivery["cancellation_requested"] or delivery["state"] != "pending":
            raise Conflict("Original input no longer admits interpretation")
        need=need_for(c,store,actor,source["source"])
        if action=="accept":
            work_id=str(uuid5(UUID(str(source["id"])),"work"))
            value={"objective":v["objective"],"acceptance":v["acceptance"],"progress":[],
                   "evidence":[],"inputId":str(source["id"])}
            c.execute("INSERT INTO hb_context_material(id,agent,kind,source,state,value) VALUES(%s,%s,'work',%s,'pending',%s)",
                      (work_id,actor["id"],source["source"],Jsonb(value)))
            change(c,need,"waiting",{**need["value"],"workId":work_id,"responseKind":"work_result","prerequisites":["result:"+work_id]})
            result={"workId":work_id,"communicationId":str(need["id"]),"workState":"pending"}
        else:
            change(c,need,"ready",{**need["value"],"material":v["body"],"responseKind":"input_answer","prerequisites":[]})
            result={"communicationId":str(need["id"]),"state":"ready"}
        change(c,source,"handled",source["value"])
        return result
    if action in ("progress","complete"):
        if not isinstance(v.get("body"),str) or not 1<=len(v["body"])<=4000:
            raise ValueError("Execution contribution must be a concise 1-4000 character summary")
        work=item(c,store,actor,v["workId"],"work")
        from .context_peers import require_current_read, own_change, requests
        require_current_read(binding,work,p["nativeTurn"])
        if action=="complete" and any(not n["value"].get("peerResult") for n in requests(c,actor,work)):
            raise Conflict("Original peer prerequisites are unresolved; work is not complete")
        event=store.readable_event(c,actor,work["source"])
        delivery=c.execute("SELECT * FROM hb_delivery WHERE event=%s AND agent=%s",
                           (event["id"],actor["id"])).fetchone()
        if work["state"] in ("complete","cancelled") or delivery and delivery["cancellation_requested"]:
            raise Conflict("Work no longer admits execution contributions")
        if action=="complete":
            from .context_goal_dependencies import require_resolved
            require_resolved(c,store,actor,work)
            if delivery["state"] not in ("claimed","running"):
                raise Conflict("Unsettled or closed original execution cannot complete work")
            if c.execute("SELECT 1 FROM hb_edit_permit WHERE authority=%s AND source->>'deliveryId'=%s AND state<>'settled' LIMIT 1",
                         (actor["authority"],str(delivery["id"]))).fetchone():
                raise Conflict("Original native edit receipts remain unsettled")
            canonical=event["data"].get("workId")
            if canonical:
                row=c.execute("SELECT * FROM hb_work WHERE id=%s AND authority=%s",
                              (canonical,actor["authority"])).fetchone()
                if (not row or row["delivery"]!=delivery["id"] or row["owner"]!=actor["id"]
                        or row["state"] not in ("submitted","accepted","blocked") or not row["report_event"]):
                    raise Conflict("Canonical work requires its original native report; context completion is not acceptance")
        progress=(work["value"].get("progress",[])+[v["body"]])[-8:]
        state="complete" if action=="complete" else "running"
        value={**work["value"],"progress":progress,"evidence":v.get("evidence",[])}
        own_change(c,actor,binding,work,state,value,p["nativeTurn"])
        if action=="complete":
            if c.execute("""SELECT 1 FROM hb_attention WHERE delivery=%s
                AND method IN ('hollywood/question','item/tool/requestUserInput')
                AND state IN ('pending','response_submitted','native_submitted')
                AND request->>'isBlocking'='false'""",(delivery["id"],)).fetchone():
                raise Conflict("Original human question prerequisite is unresolved")
            needs=c.execute("""SELECT * FROM hb_context_material WHERE agent=%s AND kind='need'
                AND value->>'workId'=%s AND state='waiting' FOR UPDATE""",
                (actor["id"],str(work["id"]))).fetchall()
            for need in needs:
                change(c,need,"ready",{**need["value"],"material":v["body"],
                                       "evidence":value["evidence"],"prerequisites":[]})
        return {"workId":str(work["id"]),"state":state}
    if action=="announce":
        source=item(c,store,actor,v["sourceId"])
        if source["kind"] not in ("work","input"):
            raise ValueError("Communication needs a source work/input")
        delivery=c.execute("SELECT cancellation_requested FROM hb_delivery WHERE event=%s AND agent=%s",
                           (source["source"],actor["id"])).fetchone()
        if source["state"]=="cancelled" or delivery and delivery["cancellation_requested"]:
            raise Conflict("Cancelled work/input cannot create new communication")
        if source["value"].get("roomObservation"):
            from .context_peers import require_current_read
            require_current_read(binding,source,p["nativeTurn"])
            event=store.readable_event(c,actor,source["source"])
            store.room(c,actor,event["room"],"can_post")
            if event["audience"]=="direct" and actor["id"] not in (event["author"],event["recipient"]):
                raise Denied("Administrative reading does not enroll you in a private exchange")
        identity=str(uuid5(UUID(str(source["id"])),p["operationId"]))
        value={"required":False,"material":v["body"],"reason":v["reason"],"prerequisites":[]}
        c.execute("INSERT INTO hb_context_material(id,agent,kind,source,state,value) VALUES(%s,%s,'need',%s,'ready',%s)",
                  (identity,actor["id"],source["source"],Jsonb(value)))
        return {"communicationId":identity,"state":"ready"}
    need=item(c,store,actor,v["communicationId"],"need")
    if need["state"]!="ready" or need["revision"]!=v["revision"]:
        raise Conflict("Communication material changed")
    read=binding["reads"].get(str(need["id"]))
    if read!={"revision":need["revision"],"turn":p["nativeTurn"]}:
        raise Denied("Read original material in this responder turn first")
    if action=="suppress":
        if need["value"].get("required"):
            raise Conflict("A required response needs explicit resolution")
        change(c,need,"suppressed",{**need["value"],"disposition":v["body"]})
        return {"communicationId":str(need["id"]),"state":"suppressed"}
    event=store.readable_event(c,actor,need["source"])
    store.room(c,actor,event["room"],"can_post")
    recipient=event["author"] if event["audience"]=="direct" else None
    if need["value"].get("materialWorkId"):
        from .context_peer_updates import validate_answer_provenance
        validate_answer_provenance(c,store,actor,need)
    if need["value"].get("workId"):
        work=item(c,store,actor,need["value"]["workId"],"work")
        if work["state"]!="complete":
            raise Conflict("Final result evidence unavailable")
    delivery=c.execute("SELECT * FROM hb_delivery WHERE event=%s AND agent=%s",
                       (event["id"],actor["id"])).fetchone()
    if delivery and delivery["cancellation_requested"]:
        raise Conflict("Original request cancelled")
    if recipient:
        store.room(c,store.actor(c,recipient),event["room"])
    provenance={"schema":"lax-purpose-context/v1","contextId":str(binding["context_id"]),
      "purpose":purpose,"nativeThread":p["nativeThread"],"nativeTurn":p["nativeTurn"],
      "communicationId":str(need["id"]),"communicationRevision":need["revision"]}
    if need["value"].get("kind")=="request":
        from .context_peers import publish
        return publish(c,store,actor,need,provenance,v.get("body"))
    published=store.event(c,actor,event["room"],"message",v.get("body",need["value"]["material"]),recipient=recipient,
       audience=event["audience"],source=event["id"],data={"nativeContext":provenance})
    change(c,need,"published",{**need["value"],"publishedEventId":str(published)})
    if need["value"].get("required"):
        completed=c.execute("""UPDATE hb_delivery SET state='completed',result_event=%s,revision=revision+1,
           updated_at=clock_timestamp() WHERE event=%s AND agent=%s AND NOT cancellation_requested
           AND state<>'completed' RETURNING *""",
           (published,event["id"],actor["id"])).fetchone()
        if completed:
            from .communication import completion
            completion(store,c,actor,event,completed,published,"completed")
    return store.receipt(c,published)


def receipt(store, actor_id, token_digest, agent, purpose, operation):
    with store.pool.connection() as c:
        actor=store.actor(c,actor_id)
        if str(actor["id"])!=agent or not c.execute(
          "SELECT 1 FROM hb_purpose_context WHERE agent=%s AND purpose=%s AND token_digest=%s",
          (agent,purpose,token_digest)).fetchone():
            raise Denied("Own purpose operation required")
        row=c.execute("SELECT result FROM hb_context_operation WHERE agent=%s AND purpose=%s AND operation=%s",
                      (agent,purpose,operation)).fetchone()
        if not row:
            permit=c.execute("SELECT * FROM hb_context_permit WHERE agent=%s AND purpose=%s AND operation=%s",
                             (agent,purpose,operation)).fetchone()
            if permit:
                return {"permit":json_value(permit),"admission":{"reason":"original_permit_observation"}}
            raise Denied("Original operation receipt unavailable")
        result=dict(row["result"])
        if isinstance(result.get("permit"),dict):
            current=c.execute("""SELECT * FROM hb_context_permit WHERE id=%s AND agent=%s
                AND purpose=%s""",(result["permit"]["id"],agent,purpose)).fetchone()
            if current is not None:
                result["currentPermit"]=json_value(current)
        return result


def configuration(store, actor_id, agent, *, connection=None):
    """Enrollment readback contains digests, never usable bearer credentials."""
    if connection is None:
        with store.pool.connection() as c:
            return configuration(store, actor_id, agent, connection=c)
    c = connection
    actor = store.actor(c, actor_id)
    system = c.execute("SELECT * FROM hb_context_system WHERE agent=%s AND authority=%s",
                       (agent, actor["authority"])).fetchone()
    if not system or not (actor["administrator"] or str(actor["id"]) == agent):
        raise Denied("Own enrolled context system required")
    bindings = c.execute("""SELECT purpose,context_id,token_digest,native_thread
        FROM hb_purpose_context WHERE agent=%s ORDER BY purpose""",(agent,)).fetchall()
    return json_value({"system":{"agentId":agent,"authorityId":system["authority"],
        "hostId":system["host"],"workspaceId":system["workspace"],"enabled":system["enabled"]},
        "bindings":[{"purpose":b["purpose"],"contextId":b["context_id"],
                     "credentialDigest":b["token_digest"],"nativeThread":b["native_thread"]} for b in bindings]})
