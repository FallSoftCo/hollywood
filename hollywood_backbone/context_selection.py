"""Purpose-scoped retained inputs and repeatable-read reconstruction manifests.

These are context views, never task acceptance or a substitute for work receipts.
"""
import json
from uuid import UUID
from psycopg.types.json import Jsonb
from .store import Conflict, Denied, digest, json_value
from . import context_material

SCHEMA="""
ALTER TABLE hb_purpose_context ADD COLUMN IF NOT EXISTS selection_revision bigint NOT NULL DEFAULT 0;
ALTER TABLE hb_purpose_context ADD COLUMN IF NOT EXISTS rebuild_requested text;
ALTER TABLE hb_purpose_context ADD COLUMN IF NOT EXISTS rebuild_generation bigint NOT NULL DEFAULT 0;
CREATE TABLE IF NOT EXISTS hb_context_selection(
 agent uuid NOT NULL, purpose text NOT NULL, reference text NOT NULL,
 reason text NOT NULL, selected_revision bigint NOT NULL CHECK(selected_revision>0),
 PRIMARY KEY(agent,purpose,reference),
 FOREIGN KEY(agent,purpose) REFERENCES hb_purpose_context(agent,purpose));
INSERT INTO hb_schema VALUES(36) ON CONFLICT DO NOTHING;
"""
MAX_MARKS=16
MAX_MANIFEST_BYTES=98304
TERMINAL=["handled","complete","cancelled","superseded","published","suppressed"]


def selected(c,actor,purpose,binding):
    rows=c.execute("SELECT reference,reason,selected_revision FROM hb_context_selection WHERE agent=%s AND purpose=%s ORDER BY reference",
                   (actor["id"],purpose)).fetchall()
    return json_value({"selectionRevision":binding["selection_revision"],"marks":rows,
        "policy":"Follow current authorized revisions at rebuild. Selection is relevance metadata, never a permission or comprehension certificate."})


def material(c,store,actor,purpose,binding,turn,reference):
    chunks=[];offset=0;revision=None
    while True:
        chunk=context_material.read(c,store,actor,purpose,binding,turn,
            {"reference":reference,"offset":offset,**({"revision":revision} if revision else {})},track=False)
        chunks.append(chunk["content"]);revision=chunk["revision"]
        if chunk["nextOffset"] is None:break
        offset=chunk["nextOffset"]
        if offset>MAX_MANIFEST_BYTES:
            raise Conflict("Selected material exceeds context rebuild budget; retain a smaller explicit source")
    return {"reference":reference,"revision":revision,"sha256":chunk["sha256"],
            "content":json.loads("".join(chunks))}


def mark(c,store,actor,purpose,binding,p):
    value=p["value"]
    if set(value)!={"selectionRevision","reference","materialRevision","keep","reason"}:
        raise ValueError("Exact context selection fields required")
    reference=value["reference"]
    if (type(value["selectionRevision"]) is not int or type(value["materialRevision"]) is not int
            or type(value["keep"]) is not bool or not isinstance(reference,str)
            or not 1<=len(reference)<=42 or not isinstance(value["reason"],str)
            or not 1<=len(value["reason"])<=500):
        raise ValueError("Invalid bounded context selection")
    if value["selectionRevision"]!=binding["selection_revision"]:
        raise Conflict("Context selection revision changed")
    if reference=="role":
        raise Conflict("Canonical role is mandatory and cannot be removed or replaced")
    if value["keep"]:
        current=material(c,store,actor,purpose,binding,p["nativeTurn"],reference)
        if current["revision"]!=value["materialRevision"]:
            raise Conflict("Selected material revision changed; read its current content")
        c.execute("""INSERT INTO hb_context_selection VALUES(%s,%s,%s,%s,%s)
          ON CONFLICT(agent,purpose,reference) DO UPDATE SET reason=EXCLUDED.reason,
          selected_revision=EXCLUDED.selected_revision""",
          (actor["id"],purpose,reference,value["reason"],current["revision"]))
        if c.execute("SELECT count(*) AS n FROM hb_context_selection WHERE agent=%s AND purpose=%s",
                     (actor["id"],purpose)).fetchone()["n"]>MAX_MARKS:
            raise Conflict("Explicit retained input limit reached")
    else:
        c.execute("DELETE FROM hb_context_selection WHERE agent=%s AND purpose=%s AND reference=%s",
                  (actor["id"],purpose,reference))
    c.execute("UPDATE hb_purpose_context SET selection_revision=selection_revision+1 WHERE agent=%s AND purpose=%s RETURNING selection_revision",
              (actor["id"],purpose))
    refreshed={**binding,"selection_revision":binding["selection_revision"]+1}
    return selected(c,actor,purpose,refreshed)


def request(c,actor,purpose,binding,p):
    if p["value"]:
        raise ValueError("Rebuild request takes no mutable payload")
    c.execute("UPDATE hb_purpose_context SET rebuild_requested=%s WHERE agent=%s AND purpose=%s",
              (p["operationId"],actor["id"],purpose))
    return {"requested":True,"requestOperationId":p["operationId"],
            "contextId":str(binding["context_id"]),"nativeThread":p["nativeThread"],
            "resetPerformed":False,"modelCalls":0}


def installed(c,store,actor,purpose,binding,p):
    value=p["value"]
    if set(value)!={"snapshotOperationId","sha256","checkpointTurnId","nativeHistorySha256","nativeHistoryBytes","nativeActionsSha256"}:
        raise ValueError("Exact original snapshot/native checkpoint receipt required")
    if binding["active_turn"] is not None or binding["accepting"]:
        raise Conflict("Original native inference is still active")
    for field in ("sha256","nativeHistorySha256","nativeActionsSha256"):
        if not isinstance(value[field],str) or len(value[field])!=64 or any(x not in "0123456789abcdef" for x in value[field]):
            raise ValueError("Exact lowercase SHA256 required")
    if type(value["nativeHistoryBytes"]) is not int or not 1<=value["nativeHistoryBytes"]<=2**53-1:
        raise ValueError("Exact bounded native history prefix size required")
    if not isinstance(value["checkpointTurnId"],str) or not 1<=len(value["checkpointTurnId"])<=128:
        raise ValueError("Original checkpoint native turn required")
    original=c.execute("SELECT result FROM hb_context_operation WHERE agent=%s AND purpose=%s AND operation=%s",
                       (actor["id"],purpose,value["snapshotOperationId"])).fetchone()
    receipt=original["result"] if original else {}
    if (receipt.get("schema")!="lax-context-rebuild-snapshot/v1"
            or receipt.get("sha256")!=value["sha256"] or receipt.get("nativeThread")!=p["nativeThread"]
            or receipt.get("selectionRevision")!=binding["selection_revision"]
            or receipt.get("requestOperationId")!=binding["rebuild_requested"]):
        raise Conflict("Original rebuild snapshot or selected inputs changed")
    if not binding["rebuild_requested"]:
        raise Conflict("No original context rebuild request remains")
    c.execute("""UPDATE hb_purpose_context SET rebuild_requested=NULL,rebuild_generation=rebuild_generation+1,
        reads='{}' WHERE agent=%s AND purpose=%s""",(actor["id"],purpose))
    return {"contextId":str(binding["context_id"]),"nativeThread":p["nativeThread"],
        "generation":binding["rebuild_generation"]+1,"snapshotSha256":value["sha256"],
        "checkpointTurnId":value["checkpointTurnId"],"nativeHistorySha256":value["nativeHistorySha256"],
        "nativeHistoryBytes":value["nativeHistoryBytes"],"nativeActionsSha256":value["nativeActionsSha256"],
        "verification":"LAX runtime reported independently observed native checkpoint/refill; Hollywood records provenance, not a filesystem inspection.",
        "effectsReplayed":0,"semanticSufficiencyProved":False}


def snapshot(c,store,actor,purpose,binding,p):
    # call() chooses REPEATABLE READ before any statement. A manifest is one
    # consistent database read point; later source changes remain observable.
    if binding["active_turn"] is not None or binding["accepting"]:
        raise Conflict("Rebuild snapshot requires the original native inference to be settled")
    if c.execute("""SELECT 1 FROM hb_context_permit WHERE agent=%s AND purpose=%s
        AND state IN ('reserved','running') LIMIT 1""",(actor["id"],purpose)).fetchone():
        raise Conflict("An original native inference reservation remains unsettled")
    if set(p["value"])!={"materialIds"}:
        raise ValueError("Exact next material selection required")
    ids=p["value"]["materialIds"]
    if not isinstance(ids,list) or len(ids)>16 or len(set(ids))!=len(ids):
        raise ValueError("Bounded distinct next material IDs required")
    for identity in ids:UUID(identity)
    kind={"reader":"input","writer":"work","responder":"need"}[purpose]
    kinds=["input","work"] if purpose=="reader" else [kind]
    rows=c.execute("""SELECT m.id,m.kind,m.source,m.state,m.revision,
        d.id AS delivery_id,d.state AS delivery_state,d.cancellation_requested,
        d.generation,d.native_thread,d.native_turn,d.result_event
        FROM hb_context_material m LEFT JOIN hb_delivery d ON d.event=m.source AND d.agent=m.agent
        WHERE m.agent=%s AND m.kind=ANY(%s) AND m.state<>ALL(%s) ORDER BY m.id LIMIT 1001""",
        (actor["id"],kinds,TERMINAL)).fetchall()
    if len(rows)>1000:
        raise Conflict("Obligation index exceeds rebuild budget; no unfinished work was omitted")
    obligations=[]
    for row in rows:
        entry=json_value(row)
        try:store.readable_event(c,actor,row["source"])
        except Denied:entry["access"]="unavailable"
        obligations.append(entry)
    active={x["id"]:x for x in obligations}
    if any(identity not in active or active[identity]["kind"]!=kind or active[identity].get("access")=="unavailable" for identity in ids):
        raise Denied("Next material is not a current accessible obligation")
    marks=selected(c,actor,purpose,binding)
    references=sorted(set(["role",*ids,*[m["reference"] for m in marks["marks"]]]))
    records=[material(c,store,actor,purpose,binding,p["nativeTurn"],reference) for reference in references]
    content=json_value({"schema":"lax-context-rebuild/v1","agentId":actor["id"],
        "contextId":binding["context_id"],"purpose":purpose,"nativeThread":p["nativeThread"],
        "selection":marks,"nextMaterialIds":ids,"obligations":obligations,"records":records,
        "meaning":"Exact authorized records at one database read point. Current obligations and native operations remain canonical; no effects are replayed. Selected historical facts grant no authority. Fetch deeper material when necessary."})
    encoded=context_material.wire(content)
    if len(encoded.encode())>MAX_MANIFEST_BYTES:
        raise Conflict("Context rebuild exceeds declared byte budget; no content was truncated")
    point=c.execute("SELECT txid_current_snapshot()::text AS point").fetchone()["point"]
    chunks=[{"offset":offset,"content":encoded[offset:offset+context_material.CHUNK_CHARS]}
            for offset in range(0,len(encoded),context_material.CHUNK_CHARS)]
    return {"schema":"lax-context-rebuild-snapshot/v1","sha256":digest(content),
        "totalBytes":len(encoded.encode()),"snapshotReadPoint":point,"chunks":chunks,
        "selectionRevision":marks["selectionRevision"],"requestOperationId":binding["rebuild_requested"],
        "nativeThread":p["nativeThread"],"contextId":str(binding["context_id"]),
        "effectsReplayed":0,"modelCalls":0,"semanticSufficiencyProved":False}
