"""Active native window cuts. Provenance is runtime-reported, not host I/O proof.

No task, context or operation store is added. Existing hb_context_operation
receipts own immutable cuts; a second operation cannot replace an unsettled cut.
"""
import hashlib
import json
from psycopg.types.json import Jsonb
from uuid import UUID

from . import context_material
from .context_selection import MAX_MANIFEST_BYTES, TERMINAL, material, selected
from .store import Conflict, Denied, digest, json_value

COMMON = {"operationId", "contextId", "permitId", "materialIds"}
SNAPSHOT = COMMON | {"windowId", "historyVersion", "reason", "phase"}
INSTALLED = COMMON | {
    "snapshotOperationId", "snapshotReadPoint", "snapshotSha256",
    "nativeActionsSha256", "selectionRevision", "totalBytes",
    "previousWindowId", "windowId", "nativeHistoryBytes", "nativeHistorySha256",
}
SNAPSHOT_SCHEMA = "lax-active-context-snapshot/v1"
INSTALLED_SCHEMA = "lax-active-context-installed/v1"


def text(value, name, limit=256):
    if not isinstance(value, str) or not 1 <= len(value.encode("utf-8")) <= limit:
        raise ValueError("Exact bounded " + name + " required")
    return value


def uint(value, name, positive=False):
    if type(value) is not int or not (1 if positive else 0) <= value <= 2**64 - 1:
        raise ValueError("Exact u64 " + name + " required")
    return value


def sha(value):
    if (not isinstance(value, str) or len(value) != 64
            or any(x not in "0123456789abcdef" for x in value)):
        raise ValueError("Exact lowercase SHA256 required")
    return value


def authorized(c, actor, binding):
    system = c.execute("""SELECT s.*,l.revision AS placement_revision FROM hb_context_system s JOIN hb_placement l
        ON l.agent=s.agent AND l.host=s.host AND l.enabled
        WHERE s.agent=%s AND s.enabled FOR SHARE OF s,l""", (actor["id"],)).fetchone()
    token = c.execute("""SELECT digest FROM hb_token WHERE digest=%s AND NOT revoked
        FOR SHARE""", (binding["token_digest"],)).fetchone()
    principal = c.execute("SELECT id FROM hb_principal WHERE id=%s AND active FOR SHARE",
                          (actor["id"],)).fetchone()
    if not system or not token or not principal:
        raise Denied("Enabled original placement and purpose credential required")
    return system


def scope(c, store, actor, purpose, binding, p):
    """Authenticate runtime facts, never identities supplied by a model tool."""
    v = p["value"]
    expected = SNAPSHOT if p["action"] == "active_snapshot" else INSTALLED
    if set(v) != expected:
        raise ValueError("Exact active window fields required")
    for name in ("contextId", "permitId"):
        if str(UUID(text(v[name], name))) != v[name]:
            raise ValueError("Canonical UUID required")
    for name in ("operationId", "windowId"):
        text(v[name], name)
    for name in ("operationId", "nativeThread", "nativeTurn"):
        text(p[name], name)
    if ((p["action"] == "active_snapshot" and v["operationId"] != p["operationId"])
            or (p["action"] == "active_installed"
                and v["operationId"] != v["snapshotOperationId"])):
        raise Conflict("Exact original native reset request identity required")
    ids = v["materialIds"]
    if (not isinstance(ids, list) or not 1 <= len(ids) <= 100
            or any(not isinstance(x, str) for x in ids)
            or len(set(ids)) != len(ids)):
        raise ValueError("Exact bounded distinct material IDs required")
    for identity in ids:
        if str(UUID(identity)) != identity:
            raise ValueError("Canonical material UUID required")
    if (v["contextId"] != str(binding["context_id"])
            or p["nativeThread"] != binding["native_thread"]
            or p["nativeTurn"] != binding["active_turn"] or not binding["accepting"]
            or ids != [str(x) for x in binding["active_material"]]):
        raise Conflict("Original active context/turn/material set changed")
    system = authorized(c, actor, binding)
    permit = c.execute("""SELECT * FROM hb_context_permit WHERE id=%s AND agent=%s
        AND purpose=%s FOR SHARE""", (v["permitId"], actor["id"], purpose)).fetchone()
    if (not permit or permit["state"] != "running"
            or permit["context_id"] != binding["context_id"]
            or permit["host"] != system["host"]
            or permit["native_thread"] != p["nativeThread"]
            or permit["native_turn"] != p["nativeTurn"]):
        raise Conflict("Exact running original inference permit required")
    reserved = [str(x) for x in permit["material_ids"]]
    # inbox_adopt extends active_material, deliberately not the original permit.
    if ((purpose == "reader" and not set(reserved) <= set(ids))
            or (purpose != "reader" and reserved != ids)):
        raise Conflict("Original permit material changed")
    kind = {"reader": "input", "writer": "work", "responder": "need"}[purpose]
    for identity in ids:
        row = c.execute("""SELECT * FROM hb_context_material
            WHERE id=%s AND agent=%s FOR SHARE""", (identity, actor["id"])).fetchone()
        if not row or row["kind"] != kind:
            raise Denied("Original purpose material required")
        store.readable_event(c, actor, row["source"])
        delivery = c.execute("""SELECT * FROM hb_delivery WHERE event=%s AND agent=%s
            FOR SHARE""", (row["source"], actor["id"])).fetchone()
        if (delivery and (delivery["cancellation_requested"]
                         or delivery["state"] in ("unknown", "cancel_requested", "cancelled"))
                or row["state"] in ("cancelled", "superseded", "suppressed")
                or purpose == "responder" and row["state"] != "ready"
                or purpose == "writer" and row["state"] not in ("pending", "running")):
            raise Conflict("Original material cancelled, retired or uncertain")
        if purpose == "writer" and (not delivery or delivery["state"] != "running"
                or delivery["host"] != system["host"]
                or delivery["native_thread"] != p["nativeThread"]
                or delivery["native_turn"] != p["nativeTurn"]):
            raise Conflict("Original writer delivery must remain running")
    return {
        "agentId": str(actor["id"]), "purpose": purpose,
        "hostId": str(system["host"]), "placementRevision": system["placement_revision"],
        "contextId": v["contextId"], "nativeThread": p["nativeThread"],
        "nativeTurn": p["nativeTurn"], "permitId": v["permitId"],
        "materialIds": ids, "permitMaterialIds": reserved, "operationId": v["operationId"],
    }


def records(c, store, actor, purpose, binding, p):
    marks = selected(c, actor, purpose, binding)
    mandatory = {"role", *p["value"]["materialIds"]}
    references = sorted(mandatory | {x["reference"] for x in marks["marks"]})
    result = []
    for reference in references:
        try:
            if purpose == "responder" and reference != "role":
                if ":" in reference:
                    raise Denied("Canonical execution records do not belong to responder")
                row = c.execute("""SELECT kind,state FROM hb_context_material
                    WHERE id=%s AND agent=%s""", (reference, actor["id"])).fetchone()
                if not row or row["kind"] != "need" or row["state"] != "ready":
                    raise Denied("Responder refill accepts active/ready needs only")
            record = material(c, store, actor, purpose, binding, p["nativeTurn"], reference)
        except Denied:
            if reference in mandatory:
                raise
            record = {"reference": reference, "access": "unavailable",
                      "meaning": "Retained pointer is unavailable; no content or authority restored."}
        result.append(record)
    return marks, result


def obligations(c, store, actor, purpose):
    kinds = {"reader": ["input", "work"], "writer": ["work"], "responder": ["need"]}[purpose]
    rows = c.execute("""SELECT m.id,m.kind,m.source,m.state,m.revision,
        d.id AS delivery_id,d.state AS delivery_state,d.cancellation_requested,
        d.generation,d.native_thread,d.native_turn,d.result_event
        FROM hb_context_material m LEFT JOIN hb_delivery d
        ON d.event=m.source AND d.agent=m.agent WHERE m.agent=%s AND m.kind=ANY(%s)
        AND m.state<>ALL(%s) AND (%s<>\'responder\' OR m.state=\'ready\')
        ORDER BY m.id LIMIT 1001""",
        (actor["id"], kinds, TERMINAL, purpose)).fetchall()
    if len(rows) > 1000:
        raise Conflict("Active obligation budget exceeded; nothing was omitted")
    result = []
    for row in rows:
        entry = json_value(row)
        try:
            store.readable_event(c, actor, row["source"])
        except Denied:
            entry["access"] = "unavailable"
        result.append(entry)
    return result


def decoded(receipt):
    offset = 0
    for chunk in receipt["chunks"]:
        if set(chunk) != {"offset", "content"} or chunk["offset"] != offset:
            raise Conflict("Original contiguous snapshot chunks required")
        offset += len(chunk["content"].encode("utf-8"))
    content = "".join(x["content"] for x in receipt["chunks"])
    raw = content.encode("utf-8")
    if (len(raw) != receipt["totalBytes"]
            or hashlib.sha256(raw).hexdigest() != receipt["sha256"]):
        raise Conflict("Original active snapshot bytes changed")
    return json.loads(content)


def current(c, store, actor, purpose, binding, p, receipt):
    """Recheck an immutable cut; later arrivals remain pending, not adopted here."""
    captured = scope(c, store, actor, purpose, binding, p)
    if receipt.get("schema") != SNAPSHOT_SCHEMA or receipt["binding"] != captured:
        raise Conflict("Original active snapshot identity changed")
    marks, values = records(c, store, actor, purpose, binding, p)
    content = decoded(receipt)
    if (receipt["selectionRevision"] != binding["selection_revision"]
            or marks != content["selection"] or values != content["records"]):
        raise Conflict("Original selected/current material changed")
    return captured


def snapshot(c, store, actor, purpose, binding, p):
    captured = scope(c, store, actor, purpose, binding, p)
    v = p["value"]
    uint(v["historyVersion"], "historyVersion")
    if (v["reason"] not in ("contextLimit", "modelDownshift", "compHashChanged")
            or v["phase"] not in ("preTurn", "midTurn", "postTurn")):
        raise ValueError("Exact native reason and phase required")
    # No second cut can supersede an acknowledged or unknown original operation.
    unfinished = c.execute("""SELECT 1 FROM hb_context_operation s WHERE s.agent=%s
        AND s.purpose=%s AND s.result->>'schema'=%s AND NOT EXISTS(
          SELECT 1 FROM hb_context_operation i WHERE i.agent=s.agent AND i.purpose=s.purpose
          AND i.result->>'schema'=%s
          AND i.result->>'snapshotOperationId'=s.result->>'snapshotOperationId') LIMIT 1""",
        (actor["id"], purpose, SNAPSHOT_SCHEMA, INSTALLED_SCHEMA)).fetchone()
    if unfinished:
        raise Conflict("Original active snapshot remains unsettled; inspect, never replace it")
    reused = c.execute("""SELECT 1 FROM hb_context_operation WHERE agent=%s AND purpose=%s
        AND result->>'schema'=%s AND result->'binding'->>'operationId'=%s LIMIT 1""",
        (actor["id"], purpose, SNAPSHOT_SCHEMA, v["operationId"])).fetchone()
    if reused:
        raise Conflict("Native operation already has its original immutable cut")
    marks, values = records(c, store, actor, purpose, binding, p)
    content = json_value({
        "schema": "lax-active-context/v1", **captured, "selection": marks,
        "nextMaterialIds": v["materialIds"],
        "obligations": obligations(c, store, actor, purpose), "records": values,
        "meaning": "Exact authorized active-turn cut; no lifecycle transition, summary or replay.",
    })
    encoded = context_material.wire(content)
    if len(encoded.encode()) > MAX_MANIFEST_BYTES:
        raise Conflict("Active snapshot byte budget exceeded; nothing was truncated")
    point = c.execute("SELECT txid_current_snapshot()::text AS point").fetchone()["point"]
    text(point, "snapshotReadPoint")
    return {
        "schema": SNAPSHOT_SCHEMA, "snapshotOperationId": p["operationId"],
        "requestOperationId": v["operationId"],
        "resetRequest": {**captured, **{k: v[k] for k in
                         ("windowId", "historyVersion", "reason", "phase")}},
        "snapshotReadPoint": point, "sha256": digest(content), "totalBytes": len(encoded.encode()),
        "chunks": [{"offset": n, "content": encoded[n:n + context_material.CHUNK_CHARS]}
                   for n in range(0, len(encoded), context_material.CHUNK_CHARS)],
        "selectionRevision": marks["selectionRevision"], "binding": captured,
        "windowId": v["windowId"], "historyVersion": v["historyVersion"],
        "reason": v["reason"], "phase": v["phase"],
        "modelCalls": 0, "effectsReplayed": 0, "semanticSufficiencyProved": False,
    }


def installed(c, store, actor, purpose, binding, p, *, apply=True):
    v = p["value"]
    scope(c, store, actor, purpose, binding, p)
    for name in ("snapshotSha256", "nativeActionsSha256", "nativeHistorySha256"):
        sha(v[name])
    for name in ("selectionRevision", "totalBytes", "nativeHistoryBytes"):
        uint(v[name], name, positive=name != "selectionRevision")
    for name in ("snapshotOperationId", "previousWindowId"):
        text(v[name], name)
    text(v["snapshotReadPoint"], "snapshotReadPoint")
    if v["totalBytes"] > MAX_MANIFEST_BYTES:
        raise ValueError("Snapshot exceeds declared host budget")
    original = c.execute("""SELECT result FROM hb_context_operation
        WHERE agent=%s AND purpose=%s AND operation=%s""",
        (actor["id"], purpose, v["snapshotOperationId"])).fetchone()
    receipt = original["result"] if original else {}
    current(c, store, actor, purpose, binding, p, receipt)
    if (v["snapshotOperationId"] != receipt["snapshotOperationId"]
            or v["snapshotReadPoint"] != receipt["snapshotReadPoint"]
            or v["snapshotSha256"] != receipt["sha256"]
            or v["selectionRevision"] != receipt["selectionRevision"]
            or v["totalBytes"] != receipt["totalBytes"]
            or v["previousWindowId"] != receipt["windowId"]
            or v["windowId"] == v["previousWindowId"]):
        raise Conflict("Original cut/digest/window changed")
    result = {
        "schema": INSTALLED_SCHEMA, "status": "recorded", "operationId": v["operationId"],
        "requestOperationId": v["operationId"], "installedOperationId": p["operationId"],
        "snapshotOperationId": v["snapshotOperationId"], "binding": receipt["binding"],
        "resetRequest": receipt["resetRequest"],
        "snapshotReadPoint": v["snapshotReadPoint"], "snapshotSha256": v["snapshotSha256"],
        "nativeActionsSha256": v["nativeActionsSha256"],
        "selectionRevision": v["selectionRevision"], "totalBytes": v["totalBytes"],
        "previousWindowId": v["previousWindowId"], "windowId": v["windowId"],
        "historyVersion": receipt["historyVersion"],
        "nativeHistoryBytes": v["nativeHistoryBytes"],
        "nativeHistorySha256": v["nativeHistorySha256"],
        "verification": "Authenticated LAX runtime reported independently verified original native "
                        "intent/checkpoint/installed receipt and prefix; Hollywood records provenance, "
                        "not filesystem inspection or model sufficiency. nativeActionsSha256 "
                        "identifies the delivered descriptor, not the full native action ledger; "
                        "resetRequest identifies the original native operation, not a transport RPC ID.",
        "effectsReplayed": 0, "modelCalls": 0, "semanticSufficiencyProved": False,
    }
    if apply:
        prior = c.execute("""SELECT result FROM hb_context_operation WHERE agent=%s AND purpose=%s
            AND result->>'schema'=%s AND result->>'snapshotOperationId'=%s LIMIT 1""",
            (actor["id"], purpose, INSTALLED_SCHEMA, v["snapshotOperationId"])).fetchone()
        if prior:
            raise Conflict("Original installation already recorded; observe its original operation")
        # The verified replacement contains these complete, current records.
        # Restore availability proofs for that exact cut and current native turn;
        # this grants no interpretation, Work completion or replay authority.
        reads = {}
        for record in decoded(receipt)["records"]:
            if record.get("access") == "unavailable":
                continue
            reference = record["reference"]
            row = record["content"] if reference != "role" and ":" not in reference else None
            if context_material.trackable(purpose, reference, row):
                reads[reference] = {"revision": record["revision"], "turn": p["nativeTurn"]}
        c.execute("UPDATE hb_purpose_context SET reads=%s WHERE agent=%s AND purpose=%s",
                  (Jsonb(reads), actor["id"], purpose))
    return result


def observe(c, store, actor, purpose, binding, p, result):
    """A duplicate POST revalidates authority; GET remains historical observation."""
    if p["action"] == "active_snapshot":
        current(c, store, actor, purpose, binding, p, result)
    elif installed(c, store, actor, purpose, binding, p, apply=False) != result:
        raise Conflict("Original installed receipt changed")


def lookup(c, store, actor, purpose, binding, result):
    """Read ONLY the original immutable receipt. Never reset, install or wake.

    Historical observation is not a fresh active_installed acceptance. No POST,
    lifecycle/permit mutation, new operation or current-window claim is made.
    Snapshot content is reauthorized; revoked content is never returned by GET.
    """
    authorized(c, actor, binding)
    captured = result["binding"]
    if (captured["agentId"] != str(actor["id"]) or captured["purpose"] != purpose
            or captured["contextId"] != str(binding["context_id"])
            or captured["nativeThread"] != binding["native_thread"]):
        raise Denied("Original purpose receipt binding unavailable")
    if result["schema"] == SNAPSHOT_SCHEMA:
        references = [r["reference"] for r in decoded(result)["records"]
                      if r.get("access") != "unavailable"]
    else:
        # Installed receipt has only identity/digest/provenance, never raw input.
        references = captured["materialIds"]
    for reference in references:
        if purpose == "responder" and reference != "role":
            row = c.execute("""SELECT kind,state FROM hb_context_material
                WHERE id=%s AND agent=%s""", (reference, actor["id"])).fetchone()
            if not row or row["kind"] != "need" or row["state"] not in ("ready", "published"):
                raise Denied("Original responder receipt material unavailable")
        material(c, store, actor, purpose, binding, captured["nativeTurn"], reference)
    return result
