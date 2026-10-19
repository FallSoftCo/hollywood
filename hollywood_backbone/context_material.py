"""Bounded purpose-scoped indexes and explicit versioned material retrieval."""
import base64
import hashlib
import json
from uuid import UUID

from psycopg.types.json import Jsonb
from .store import Conflict, Denied, json_value

CHUNK_CHARS = 3072
INDEX_BYTES = 6144


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
           OR (%s='reader' AND kind='work' AND state NOT IN ('complete','cancelled')))
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


def read(c, store, actor, purpose, binding, turn, value):
    reference = value.get("reference")
    if not isinstance(reference, str) or not 1 <= len(reference) <= 36:
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
            "SELECT primary_role,purpose,revision FROM hb_agent_role WHERE principal=%s",
            (actor["id"],)).fetchone()
        revision = row["revision"]
        material = {"agentId": str(actor["id"]), "role": json_value(row)}
    else:
        identity = str(UUID(reference))
        row = c.execute("SELECT * FROM hb_context_material WHERE id=%s AND agent=%s",
                        (identity, actor["id"])).fetchone()
        if not row or not visible(purpose, row):
            raise Denied("Material unavailable to this context purpose")
        event = store.readable_event(c, actor, row["source"])
        revision = row["revision"]
        material = json_value(row)
        if purpose == "reader" and row["kind"] == "input":
            material["event"] = json_value(event)
            if event["data"].get("type") == "peerCompletion" and event["source"]:
                material["peerResult"] = json_value(store.readable_event(c,actor,event["source"]))
        if row["kind"] == "work":
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
    if reference != "role" and (purpose == "reader"
                                or purpose == "responder" and row["state"] == "ready"
                                or purpose == "writer" and row["kind"] == "work"):
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
