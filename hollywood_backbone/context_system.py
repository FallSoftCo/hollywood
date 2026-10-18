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
    for kind, state, value in [
        ("input","pending",{}),
        ("need","interpreting",{"inputId":identity,"required":True,"prerequisites":[]}),
    ]:
        key = identity if kind == "input" else str(uuid5(UUID(identity),"response"))
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

def snapshot(c, store, actor, purpose, binding, turn):
    rows = c.execute("""SELECT * FROM hb_context_material WHERE agent=%s
      AND (state NOT IN ('published','handled') OR kind='need')
      ORDER BY created_at DESC,id LIMIT 40""",(actor["id"],)).fetchall()
    result={"inputs":[],"work":[],"communications":[]}
    reads={}
    for row in rows:
        try:
            event=store.readable_event(c,actor,row["source"])
        except Denied:
            continue
        value=json_value(row)
        if purpose=="reader" and row["kind"]=="input" and row["state"]=="pending":
            value["event"]=json_value(event)
            result["inputs"].append(value)
        if purpose in ("reader","writer") and row["kind"]=="work":
            if purpose=="writer" and row["state"]=="cancelled":
                continue
            result["work"].append(value)
        if purpose=="responder" and row["kind"]=="need" and row["state"] in ("ready","published"):
            # No automatic original-conversation feed to responder.
            value["audience"]={"roomId":str(event["room"]),"audience":event["audience"]}
            result["communications"].append(value)
            if row["state"]=="ready":
                reads[str(row["id"]) ]={"revision":row["revision"],"turn":turn}
    if purpose=="responder":
        c.execute("UPDATE hb_purpose_context SET reads=%s WHERE agent=%s AND purpose=%s",
                  (Jsonb(reads),actor["id"],purpose))
    role=c.execute("SELECT primary_role,purpose,revision FROM hb_agent_role WHERE principal=%s",
                   (actor["id"],)).fetchone()
    return {"agentId":str(actor["id"]),"contextPurpose":purpose,"role":json_value(role),**result}

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

def perform(store,c,actor,purpose,binding,p):
    action,v=p["action"],p["value"]
    permitted={
      "reader":{"pending","snapshot","accept","answer","announce"},
      "writer":{"pending","snapshot","progress","complete","announce"},
      "responder":{"pending","snapshot","publish","suppress"},
    }
    if action not in permitted[purpose]:
        raise Denied("Tool does not belong to this context purpose")
    if action=="pending":
        rows=c.execute("""SELECT m.id,m.kind,m.state,m.revision,
          COALESCE(d.cancellation_requested,false) AS cancelled
          FROM hb_context_material m LEFT JOIN hb_delivery d
          ON d.event=m.source AND d.agent=m.agent WHERE m.agent=%s
          AND ((m.kind='input' AND m.state='pending')
            OR (m.kind='work' AND m.state IN ('pending','running'))
            OR (m.kind='need' AND m.state='ready')) ORDER BY m.created_at,m.id LIMIT 100""",
          (actor["id"],)).fetchall()
        kind={"reader":"input","writer":"work","responder":"need"}[purpose]
        return {"pending":json_value([r for r in rows if r["kind"]==kind])}
    if action=="snapshot":
        return snapshot(c,store,actor,purpose,binding,p["nativeTurn"])
    if action in ("accept","answer"):
        source=item(c,store,actor,v["inputId"],"input")
        if source["state"]!="pending":
            raise Conflict("Input already interpreted")
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
        event=store.readable_event(c,actor,work["source"])
        delivery=c.execute("SELECT * FROM hb_delivery WHERE event=%s AND agent=%s",
                           (event["id"],actor["id"])).fetchone()
        if work["state"] in ("complete","cancelled") or delivery and delivery["cancellation_requested"]:
            raise Conflict("Work no longer admits execution contributions")
        progress=(work["value"].get("progress",[])+[v["body"]])[-8:]
        state="complete" if action=="complete" else "running"
        value={**work["value"],"progress":progress,"evidence":v.get("evidence",[])}
        change(c,work,state,value)
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
    published=store.event(c,actor,event["room"],"message",v["body"],recipient=recipient,
       audience=event["audience"],source=event["id"],data={"nativeContext":provenance})
    change(c,need,"published",{**need["value"],"publishedEventId":str(published)})
    if need["value"].get("required"):
        c.execute("""UPDATE hb_delivery SET state='completed',result_event=%s,revision=revision+1,
           updated_at=clock_timestamp() WHERE event=%s AND agent=%s AND NOT cancellation_requested""",
           (published,event["id"],actor["id"]))
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
