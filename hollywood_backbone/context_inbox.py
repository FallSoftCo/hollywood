"""Exact primary input snapshots for mechanical delivery to a native reader."""
from uuid import UUID
from psycopg.types.json import Jsonb
from .store import Conflict, Denied
from . import context_material

MAX_INPUTS = 16
MAX_WORK = 100


def snapshot(c, store, actor, binding, p):
    if set(p["value"]) != {"materialIds"}:
        raise ValueError("Exact reader input IDs required")
    ids = p["value"]["materialIds"]
    if (not isinstance(ids, list) or not 1 <= len(ids) <= MAX_INPUTS
            or len(set(ids)) != len(ids)):
        raise ValueError("Bounded primary input batch required")
    for identity in ids:
        UUID(identity)
        row = c.execute("SELECT * FROM hb_context_material WHERE id=%s AND agent=%s",
                        (identity, actor["id"])).fetchone()
        if not row or row["kind"] != "input" or row["state"] != "pending":
            raise Conflict("Original pending reader input required")
        store.readable_event(c, actor, row["source"])
        delivery = c.execute("SELECT cancellation_requested FROM hb_delivery WHERE event=%s AND agent=%s",
                             (row["source"], actor["id"])).fetchone()
        if delivery and delivery["cancellation_requested"]:
            raise Conflict("Original input cancelled")
    work = c.execute("""SELECT id FROM hb_context_material WHERE agent=%s AND kind='work'
        AND state NOT IN ('complete','cancelled','superseded') ORDER BY created_at,id LIMIT %s""",
        (actor["id"], MAX_WORK + 1)).fetchall()
    if len(work) > MAX_WORK:
        raise Conflict("Reader work snapshot requires explicit pagination; no content was omitted")
    references = ["role"] + ids
    for row in work:
        # Work access may have been revoked independently of its durable existence.
        material = c.execute("SELECT source FROM hb_context_material WHERE id=%s",
                             (row["id"],)).fetchone()
        try:
            store.readable_event(c, actor, material["source"])
        except Denied:
            continue
        references.append(str(row["id"]))
    chunks = []
    for reference in references:
        value = {"reference": reference}
        while True:
            chunk = context_material.read(c, store, actor, "reader", binding,
                                          p["nativeTurn"], value, track=False)
            chunks.append(chunk)
            if chunk["nextOffset"] is None:
                break
            value = {"reference": reference, "revision": chunk["revision"],
                     "offset": chunk["nextOffset"]}
    return {"schema": "lax-reader-input/v1", "nativeThread": p["nativeThread"],
            "inputIds": ids, "chunks": chunks,
            "meaning": "Exact attributable input and current role/work. Delivery is not interpretation; observations are not assignments."}


def adopt(c, store, actor, binding, p):
    if set(p["value"]) != {"snapshotOperationId"}:
        raise ValueError("Original reader snapshot receipt required")
    original = c.execute("""SELECT result FROM hb_context_operation
        WHERE agent=%s AND purpose='reader' AND operation=%s""",
        (actor["id"], p["value"]["snapshotOperationId"])).fetchone()
    receipt = original["result"] if original else {}
    if (receipt.get("schema") != "lax-reader-input/v1"
            or receipt.get("nativeThread") != p["nativeThread"]):
        raise Denied("Snapshot belongs to another native context")
    ids = [str(x) for x in binding["active_material"]]
    reads = dict(binding["reads"])
    delivered = {}
    for chunk in receipt["chunks"]:
        delivered[chunk["reference"]] = chunk["revision"]
    for identity in receipt["inputIds"]:
        row = c.execute("SELECT * FROM hb_context_material WHERE id=%s AND agent=%s",
                        (identity, actor["id"])).fetchone()
        store.readable_event(c, actor, row["source"])
        d = c.execute("SELECT cancellation_requested FROM hb_delivery WHERE event=%s AND agent=%s",
                      (row["source"], actor["id"])).fetchone()
        if row["state"] != "pending" or d and d["cancellation_requested"]:
            if identity not in ids:
                continue  # Already resolved/cancelled while the exact snapshot travelled.
        elif identity not in ids:
            ids.append(identity)
    if len(ids) > 100:
        raise Conflict("Current reader batch full; retain new inputs for its next inference")
    for reference, revision in delivered.items():
        if reference == "role":
            continue
        row = c.execute("SELECT * FROM hb_context_material WHERE id=%s AND agent=%s",
                        (reference, actor["id"])).fetchone()
        store.readable_event(c, actor, row["source"])
        # Concurrent writer changes require a current read before an amendment.
        if row["revision"] == revision:
            reads[reference] = {"revision": revision, "turn": p["nativeTurn"]}
    c.execute("""UPDATE hb_purpose_context SET active_material=%s::uuid[],reads=%s
        WHERE agent=%s AND purpose='reader'""", (ids, Jsonb(reads), actor["id"]))
    return {"nativeTurn": p["nativeTurn"], "materialIds": ids,
            "snapshotOperationId": p["value"]["snapshotOperationId"],
            "deliveredRevisions": delivered, "interpretationInferred": False}
