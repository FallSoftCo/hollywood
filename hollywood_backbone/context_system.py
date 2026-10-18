"""Purpose-bound native agent communication; no model or execution in Hollywood."""
import json
from uuid import UUID, uuid5
from psycopg.types.json import Jsonb
from .store import Conflict, Denied, digest, json_value

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
"""

def configure(store, actor_id, agent, payload):
    with store.pool.connection() as c:
        actor = store.actor(c, actor_id)
        if not actor["administrator"]:
            raise Denied("Installed administrator required")
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
            raise Conflict("Existing context system requires explicit migration")
        bindings = payload["bindings"]
        if {b["purpose"] for b in bindings} != PURPOSES or len(bindings) != 3:
            raise ValueError("Exactly three purpose bindings required")
        c.execute("INSERT INTO hb_context_system(agent,authority,host,workspace,role_revision) VALUES(%s,%s,%s,%s,%s)",
                  (agent,target["authority"],payload["hostId"],payload["workspaceId"],target["revision"]))
        for b in bindings:
            c.execute("INSERT INTO hb_token(digest,principal) VALUES(%s,%s)",(b["credentialDigest"],agent))
            c.execute("INSERT INTO hb_purpose_context(agent,purpose,context_id,token_digest) VALUES(%s,%s,%s,%s)",
                      (agent,b["purpose"],b["contextId"],b["credentialDigest"]))
        return {"agentId":agent,"purposes":sorted(PURPOSES),"configured":True}

def ingest(c, agent, event):
    if not c.execute("SELECT 1 FROM hb_context_system WHERE agent=%s AND enabled",(agent,)).fetchone():
        return
    identity = str(event)
    from .context_peers import incoming
    peer = incoming(c, agent, event)
    materials = [("input", "pending", peer or {})]
    if peer is None:
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
          JOIN hb_agent_role r ON r.principal=s.agent AND r.revision=s.role_revision
          WHERE s.agent=%s AND s.enabled""",(agent,)).fetchone()
        binding=c.execute("""SELECT b.* FROM hb_purpose_context b JOIN hb_token t
          ON t.digest=b.token_digest AND NOT t.revoked
          WHERE b.agent=%s AND b.purpose=%s AND b.token_digest=%s FOR UPDATE OF b""",
          (agent,purpose,token_digest)).fetchone()
        if not system or not binding:
            raise Denied("Purpose credential or installed placement unavailable")
        if p["action"]=="pending":
            if p["nativeThread"]!=binding["native_thread"]:
                raise Conflict("Native context binding changed")
            return json_value(perform(store,c,actor,purpose,binding,p))
        op=p["operationId"]
        fingerprint=digest(p)
        old=c.execute("SELECT * FROM hb_context_operation WHERE agent=%s AND purpose=%s AND operation=%s",
                      (agent,purpose,op)).fetchone()
        if old:
            if old["digest"]!=fingerprint:
                raise Conflict("Original context operation changed")
            return old["result"]
        if p["action"]=="bind":
            if binding["native_thread"] and binding["native_thread"]!=p["nativeThread"]:
                raise Conflict("Original context thread changed")
            c.execute("UPDATE hb_purpose_context SET native_thread=%s WHERE agent=%s AND purpose=%s",
                      (p["nativeThread"],agent,purpose))
            result={"contextId":str(binding["context_id"]),"nativeThread":p["nativeThread"],"purpose":purpose}
        elif p["nativeThread"]!=binding["native_thread"]:
            raise Conflict("Native context binding changed")
        else:
            result=perform(store,c,actor,purpose,binding,p)
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
                    "nativeThread": p["nativeThread"], "nativeTurn": turn, "status": "inProgress"}})
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
    for identity in binding["active_material"]:
        row = item(c, store, actor, str(identity))
        delivery = c.execute("SELECT cancellation_requested FROM hb_delivery WHERE event=%s AND agent=%s",
                             (row["source"], actor["id"])).fetchone()
        cancelled = delivery and delivery["cancellation_requested"]
        if row["kind"] == "work" and row["state"] not in ("complete", "cancelled"):
            change(c, row, "cancelled" if cancelled else "waiting" if row["state"]=="waiting" else "pending",
                   {**row["value"], "execution": {"nativeThread": p["nativeThread"],
                    "nativeTurn": turn, "status": status}})
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
    return {"nativeTurn": turn, "state": status}


def perform(store,c,actor,purpose,binding,p):
    action,v=p["action"],p["value"]
    if action in ("begin", "close", "end"):
        return lifecycle(c, store, actor, purpose, binding, p)
    if action == "activity":
        # Recovery may append this immutable log after the original turn closes.
        # It neither admits callbacks nor changes execution/work settlement.
        from .context_execution import append_activity
        return append_activity(c, store, actor, purpose, binding, p)
    if action not in ("pending",) and (not binding["accepting"] or binding["active_turn"] != p["nativeTurn"]):
        raise Conflict("Original native turn no longer admits callbacks")
    permitted={
      "reader":{"pending","snapshot","read","accept","answer","announce","peer_result","directory"},
      "writer":{"pending","snapshot","read","progress","complete","announce","peer_request","wait_peers","directory"},
      "responder":{"pending","snapshot","read","publish","suppress"},
    }
    if action not in permitted[purpose]:
        raise Denied("Tool does not belong to this context purpose")
    target=v.get("workId") if action in ("progress","complete","peer_request","wait_peers") else v.get("inputId") if action in ("accept","answer","peer_result") else v.get("communicationId")
    if target and target not in {str(x) for x in binding["active_material"]}:
        raise Denied("Material does not belong to this captured native turn")
    if action in ("directory", "peer_request", "wait_peers", "peer_result"):
        from . import context_peers
        if action == "directory":
            return context_peers.directory(c,store,actor,v)
        function = {"peer_request": context_peers.queue, "wait_peers": context_peers.wait,
                    "peer_result": context_peers.apply}[action]
        return function(c,store,actor,binding,p)
    if action=="pending":
        if purpose=="writer":
            cancelled=c.execute("""SELECT m.* FROM hb_context_material m JOIN hb_delivery d
              ON d.event=m.source AND d.agent=m.agent
              WHERE m.agent=%s AND m.kind='work' AND m.state='waiting' AND d.cancellation_requested
              AND m.value->'execution'->>'status' IN ('completed','interrupted','failed')
              AND NOT EXISTS(SELECT 1 FROM hb_purpose_context b WHERE b.agent=m.agent
                  AND b.active_turn IS NOT NULL AND m.id=ANY(b.active_material)) FOR UPDATE OF m""",
              (actor["id"],)).fetchall()
            for work in cancelled:
                c.execute("""UPDATE hb_context_material SET state='cancelled',revision=revision+1,
                  updated_at=clock_timestamp() WHERE agent=%s AND source=%s
                  AND state NOT IN ('handled','published','complete','cancelled')""",
                  (actor["id"],work["source"]))
                c.execute("""UPDATE hb_delivery SET state='cancelled',revision=revision+1,
                  updated_at=clock_timestamp() WHERE agent=%s AND event=%s AND cancellation_requested""",
                  (actor["id"],work["source"]))
        kind={"reader":"input","writer":"work","responder":"need"}[purpose]
        rows=c.execute("""SELECT m.id,m.kind,m.state,m.revision,
          coalesce((m.value->>'resumeRevision')::bigint,0) AS resume_revision,
          COALESCE(d.cancellation_requested,false) AS cancelled
          FROM hb_context_material m LEFT JOIN hb_delivery d
          ON d.event=m.source AND d.agent=m.agent WHERE m.agent=%s AND m.kind=%s
          AND ((m.kind='input' AND m.state='pending')
            OR (m.kind='work' AND m.state IN ('pending','running'))
            OR (m.kind='need' AND m.state='ready')) ORDER BY m.created_at,m.id LIMIT 100""",
          (actor["id"],kind)).fetchall()
        return {"pending":json_value(rows)}
    if action in ("snapshot", "read"):
        from .context_material import index, read
        retrieve = index if action == "snapshot" else read
        return retrieve(c,store,actor,purpose,binding,p["nativeTurn"],v)
    if action in ("accept","answer"):
        source=item(c,store,actor,v["inputId"],"input")
        if source["state"]!="pending":
            raise Conflict("Input already interpreted")
        if source["value"].get("peerCompletion"):
            raise Conflict("Record the peer result on its original work; no new task or acknowledgement is required")
        need=need_for(c,store,actor,source["source"])
        if action=="accept":
            work_id=str(uuid5(UUID(str(source["id"])),"work"))
            value={"objective":v["objective"],"acceptance":v["acceptance"],"progress":[],
                   "evidence":[],"inputId":str(source["id"])}
            c.execute("INSERT INTO hb_context_material(id,agent,kind,source,state,value) VALUES(%s,%s,'work',%s,'pending',%s)",
                      (work_id,actor["id"],source["source"],Jsonb(value)))
            change(c,need,"waiting",{**need["value"],"workId":work_id,"prerequisites":["result:"+work_id]})
            c.execute("""UPDATE hb_delivery SET state='running',host=(SELECT host FROM hb_context_system WHERE agent=%s),
              claim_id=%s,generation=1,revision=revision+1,updated_at=clock_timestamp()
              WHERE event=%s AND agent=%s AND state='pending'""",
              (actor["id"],"context-work:"+work_id,source["source"],actor["id"]))
            result={"workId":work_id,"communicationId":str(need["id"]),"workState":"pending"}
        else:
            change(c,need,"ready",{**need["value"],"material":v["body"],"prerequisites":[]})
            result={"communicationId":str(need["id"]),"state":"ready"}
        change(c,source,"handled",source["value"])
        return result
    if action in ("progress","complete"):
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
        progress=(work["value"].get("progress",[])+[v["body"]])[-8:]
        state="complete" if action=="complete" else "running"
        value={**work["value"],"progress":progress,"evidence":v.get("evidence",[])}
        own_change(c,actor,binding,work,state,value,p["nativeTurn"])
        if action=="complete":
            need=need_for(c,store,actor,work["source"])
            change(c,need,"ready",{**need["value"],"material":v["body"],
                                   "evidence":value["evidence"],"prerequisites":[]})
        return {"workId":str(work["id"]),"state":state}
    if action=="announce":
        source=item(c,store,actor,v["sourceId"])
        if source["kind"] not in ("work","input"):
            raise ValueError("Communication needs a source work/input")
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
            raise Denied("Original operation receipt unavailable")
        return row["result"]
