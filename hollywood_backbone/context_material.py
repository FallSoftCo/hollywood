"""Bounded purpose-scoped indexes and explicit versioned material retrieval."""
import base64
import hashlib
import json
from uuid import UUID

from psycopg.types.json import Jsonb
from .store import Conflict, Denied, json_value

CHUNK_CHARS = 3072
INDEX_BYTES = 6144
COMPLETE_BYTES = 98304

class MaterialReadConflict(Conflict):
    code="MATERIAL_SNAPSHOT_TOO_LARGE"
    def __init__(self):
        super().__init__("Material exceeds the complete snapshot budget. Use explicit offset/revision reads; no content was truncated.")

def trackable(purpose,reference,row):
    return (reference=="role" or row is not None and (purpose=="reader"
        or purpose=="responder" and row["state"]=="ready"
        or purpose=="writer" and row["kind"]=="work"))


def wire(value):
    return json.dumps(json_value(value), sort_keys=True, separators=(",", ":"))


def visible(purpose, row):
    return (purpose == "reader" and row["kind"] in ("input", "work")
            or purpose == "writer" and row["kind"] == "work"
            or purpose == "responder" and row["kind"] == "need"
            and row["state"] in ("ready", "published"))


def index(c, store, actor, purpose, binding, turn, value):
    view = value.get("view", "active")
    if view not in ("active", "history"):
        raise ValueError("Unknown material index view")
    after = value.get("after")
    if after is not None and (not isinstance(after, str) or not 1 <= len(after) <= 512):
        raise ValueError("Invalid material cursor")
    boundary = None
    if after:
        try:
            cursor = json.loads(base64.urlsafe_b64decode(after))
            if set(cursor) != {"turn", "view", "id"} or cursor["turn"] != turn or cursor["view"] != view:
                raise ValueError()
            identity = str(UUID(cursor["id"]))
        except (ValueError, TypeError, KeyError):
            raise ValueError("Material cursor belongs to another view or turn") from None
        boundary = c.execute(
            "SELECT id,created_at FROM hb_context_material WHERE id=%s AND agent=%s",
            (identity, actor["id"])).fetchone()
        if not boundary:
            raise Denied("Original material cursor unavailable")
    active = binding["active_material"]
    kinds = {"reader": ["input", "work"], "writer": ["work"], "responder": ["need"]}[purpose]
    query = """SELECT id,kind,source,state,revision,created_at,
      CASE WHEN id=ANY(%s::uuid[]) THEN 0 ELSE 1 END AS priority
      FROM hb_context_material WHERE agent=%s AND kind=ANY(%s)
      AND (%s='history' OR id=ANY(%s::uuid[])
           OR (%s='reader' AND kind='work' AND state NOT IN ('complete','cancelled','superseded')))
      AND (%s<>'responder' OR state IN ('ready','published'))"""
    args = [active, actor["id"], kinds, view, active, purpose, purpose]
    if boundary:
        query += """ AND (CASE WHEN id=ANY(%s::uuid[]) THEN 0 ELSE 1 END,created_at,id)
                      > (%s,%s,%s)"""
        priority = 0 if str(boundary["id"]) in {str(x) for x in active} else 1
        args += [active, priority, boundary["created_at"], boundary["id"]]
    query += " ORDER BY priority,created_at,id LIMIT 21"
    rows = c.execute(query, args).fetchall()
    role = c.execute("SELECT primary_role,revision FROM hb_agent_role WHERE principal=%s",
                     (actor["id"],)).fetchone()
    result = {"agentId": str(actor["id"]), "contextPurpose": purpose,
              "role": json_value(role), "roleReference": "role", "view": view,
              "inputs": [], "work": [], "communications": [], "nextCursor": None,
              "retrieval": "Index only. Read required references with context_material_read; history is explicit. Pages are current reads, not an atomic snapshot."}
    last = None
    for row in rows:
        # Permission changes can hide an old source; a cursor never restores access.
        try:
            store.readable_event(c, actor, row["source"])
        except Denied:
            last = row  # Advance past newly inaccessible sources without exposing their content.
            continue
        entry = {k: json_value(row[k]) for k in ("id", "kind", "source", "state", "revision")}
        entry["reference"] = str(row["id"])
        key = {"input": "inputs", "work": "work", "need": "communications"}[row["kind"]]
        result[key].append(entry)
        if len(wire(result)) > INDEX_BYTES - 256:
            result[key].pop()
            break
        last = row
    if last and last["id"] != rows[-1]["id"]:
        result["nextCursor"] = base64.urlsafe_b64encode(wire({
            "turn": turn, "view": view, "id": str(last["id"])}).encode()).decode()
    # A full DB page may have another page even if all 21 entries fit.
    elif last and len(rows) == 21:
        result["nextCursor"] = base64.urlsafe_b64encode(wire({
            "turn": turn, "view": view, "id": str(last["id"])}).encode()).decode()
    assert len(wire(result)) <= INDEX_BYTES
    return result


def read(c, store, actor, purpose, binding, turn, value, *, track=True):
    reference = value.get("reference")
    if not isinstance(reference, str) or not 1 <= len(reference) <= 42:
        raise ValueError("Invalid material reference")
    offset = value.get("offset", 0)
    expected = value.get("revision")
    if type(offset) is not int or offset < 0:
        raise ValueError("Invalid material offset")
    if expected is not None and (type(expected) is not int or expected < 1):
        raise ValueError("Invalid material revision")
    if offset and expected is None:
        raise ValueError("Continuation requires the original material revision")
    if reference == "role":
        row = c.execute(
            "SELECT primary_role,purpose,revision,native_goal_scope,home_room,decision_owner FROM hb_agent_role WHERE principal=%s",
            (actor["id"],)).fetchone()
        revision = row["revision"]
        material = {"agentId": str(actor["id"]), "role": json_value(row)}
    elif reference.startswith(("goal:","work:")):
        if purpose=="responder":
            raise Denied("Canonical execution context belongs to reader/writer retrieval")
        from .work import goal_row,work_row
        kind,identity=reference.split(":",1)
        identity=str(UUID(identity))
        if kind=="goal":
            from .goal_contract import current
            value=goal_row(store,c,actor,identity)
            material={"goal":json_value(value),"contract":current(c,value)}
        else:
            value=work_row(store,c,actor,identity)
            material={"work":json_value(value)}
        revision=value["revision"]
        row=None
    elif reference.startswith("event:"):
        if purpose!="reader":
            raise Denied("Room retrieval belongs to the reader")
        event=store.readable_event(c,actor,str(UUID(reference[6:])))
        revision=1
        material={"event":json_value(event),"meaning":"Original attributable content; not an assignment or permission."}
        row=None
    else:
        identity = str(UUID(reference))
        row = c.execute("SELECT * FROM hb_context_material WHERE id=%s AND agent=%s",
                        (identity, actor["id"])).fetchone()
        if not row or not visible(purpose, row):
            raise Denied("Material unavailable to this context purpose")
        event = store.readable_event(c, actor, row["source"])
        revision = row["revision"]
        material = json_value(row)
        if purpose == "reader" and row["kind"] == "input" and not row["value"].get("writerOutcome"):
            material["event"] = json_value(event)
            from .context_assignments import current
            assignment=current(c,actor,row)
            if assignment:
                material["canonicalAssignment"]=assignment
            if event["data"].get("type") == "peerCompletion" and event["source"]:
                material["peerResult"] = json_value(store.readable_event(c,actor,event["source"]))
        if row["kind"] == "work":
            material["materialDomain"] = "lax.context-responsibility/v1"
            if event["data"].get("workId"):
                material["canonicalWorkReference"]={k:event["data"][k] for k in ("workId","goalId","attemptId")
                                                   if event["data"].get(k) is not None}
            material["scope"] = {"roomId":str(event["room"]),"audience":event["audience"],
                                 "sourceEventId":str(event["id"])}
        if purpose == "responder":
            material["audience"] = {"roomId": str(event["room"]), "audience": event["audience"]}
    if expected is not None and expected != revision:
        raise Conflict("Material changed; restart its read at offset zero")
    encoded = wire(material)
    if offset >= len(encoded) and offset != 0:
        raise ValueError("Material offset outside original content")
    stop = min(offset + CHUNK_CHARS, len(encoded))
    following = stop if stop < len(encoded) else None
    if track and trackable(purpose,reference,row):
        reads = dict(binding["reads"])
        prior = reads.get(reference)
        if offset and prior != {"revision": revision, "turn": turn, "nextOffset": offset}:
            raise Conflict("Read original material sequentially before contributing")
        reads[reference] = {"revision": revision, "turn": turn,
                            **({"nextOffset": following} if following is not None else {})}
        c.execute("UPDATE hb_purpose_context SET reads=%s WHERE agent=%s AND purpose=%s",
                  (Jsonb(reads), actor["id"], purpose))
    return {"reference": reference, "revision": revision, "offset": offset,
            "encoding": "json/ascii", "content": encoded[offset:stop],
            "nextOffset": following, "totalBytes": len(encoded),
            "sha256": hashlib.sha256(encoded.encode()).hexdigest(),
            "meaning": "Exact serialized material; concatenate matching revision chunks. A read does not certify its assertions."}


def complete(c,store,actor,purpose,binding,turn,value):
    """One database snapshot, one read proof, bounded fragments; never a summary."""
    if set(value)-{"reference","revision"}:
        raise ValueError("Complete material read accepts reference and optional revision")
    reference=value.get("reference")
    part=read(c,store,actor,purpose,binding,turn,value,track=False)
    if part["totalBytes"]>COMPLETE_BYTES:
        raise MaterialReadConflict()
    first=dict(part);chunks=[]
    while True:
        chunks.append({"offset":part["offset"],"content":part["content"]})
        offset=part["nextOffset"]
        if offset is None:break
        part=read(c,store,actor,purpose,binding,turn,
            {"reference":reference,"revision":first["revision"],"offset":offset},track=False)
    encoded="".join(x["content"] for x in chunks)
    if len(encoded.encode())!=first["totalBytes"] or hashlib.sha256(encoded.encode()).hexdigest()!=first["sha256"]:
        raise Conflict("Complete material changed; no read proof installed")
    material=json.loads(encoded)
    row=material if reference!="role" and ":" not in reference else None
    if trackable(purpose,reference,row):
        reads={**binding["reads"],reference:{"revision":first["revision"],"turn":turn}}
        c.execute("UPDATE hb_purpose_context SET reads=%s WHERE agent=%s AND purpose=%s",
            (Jsonb(reads),actor["id"],purpose))
    return {"schema":"lax-context-material-snapshot/v1","reference":reference,
        "revision":first["revision"],"sha256":first["sha256"],"totalBytes":first["totalBytes"],
        "chunks":chunks,"meaning":"Exact purpose-authorized material at one coherent read cut; read proof does not establish comprehension or execution authority."}
