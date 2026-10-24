"""Typed metadata for original-host evidence; payloads never enter Hollywood.

The enrolled LAX runtime attests native file reads and rendering measurements.
Hollywood independently checks captured execution ownership, current source access,
selection CAS and its own canonical read cut. It cannot inspect a host filesystem.
"""
import json
import re
from uuid import UUID, uuid5
from psycopg.types.json import Jsonb
from .store import Conflict, Denied, digest

SCHEMA = """
ALTER TABLE hb_purpose_context ADD COLUMN IF NOT EXISTS native_action_retention integer
 NOT NULL DEFAULT 0 CHECK(native_action_retention IN (0,1));
ALTER TABLE hb_context_selection ADD COLUMN IF NOT EXISTS native_action jsonb;
INSERT INTO hb_schema VALUES(37) ON CONFLICT DO NOTHING;
"""
FIELDS = {"schema", "reference", "authorityId", "agentId", "contextId", "hostId",
          "nativeThread", "nativeTurn", "itemId", "scopeId", "materialId",
          "sourceRequestId", "deliveryId", "sha256", "payloadBytes", "pinSha256"}
LIMITS = {"rawBytes": 65536, "bundleBytes": 98304, "descriptorBytes": 16384,
          "decodedBytes": 262144, "renderedItems": 256, "wireBytes": 1048576}


def bind(c, actor, purpose, binding, value):
    version = value.get("nativeActionRetention", 0)
    if type(version) is not int or version not in (0, 1) or (version and purpose != "writer"):
        raise ValueError("Writer-only native action retention version required")
    previous = binding.get("native_action_retention", 0)
    if previous != version:
        if binding["active_turn"] is not None or previous:
            raise Conflict("Explicit drained paired retention downgrade required")
        c.execute("UPDATE hb_purpose_context SET native_action_retention=%s WHERE agent=%s AND purpose=%s",
                  (version, actor["id"], purpose))
    return version


def authorize(c, store, actor, purpose, binding, metadata):
    if purpose != "writer" or binding.get("native_action_retention", 0) != 1:
        raise Denied("Paired Writer native-action retention contract required")
    if not isinstance(metadata, dict) or set(metadata) != FIELDS:
        raise ValueError("Exact native pin metadata required; no payload accepted")
    if metadata["schema"] != "lax-native-action-pin-metadata/v1":
        raise ValueError("Unknown native pin metadata version")
    for key in ("sha256", "pinSha256"):
        if not isinstance(metadata[key], str) or not re.fullmatch("[a-f0-9]{64}", metadata[key]):
            raise ValueError("Exact original native pin digests required")
    if type(metadata["payloadBytes"]) is not int or not 0 < metadata["payloadBytes"] <= 32768:
        raise Conflict("Whole native item exceeds retained-item budget")
    for key in ("nativeTurn", "itemId", "scopeId"):
        if not isinstance(metadata[key], str) or not 0 < len(metadata[key]) <= 128 or "/" in metadata[key]:
            raise ValueError("Bounded original native identity required")
    system = c.execute("SELECT host FROM hb_context_system WHERE agent=%s", (actor["id"],)).fetchone()
    expected = {"authorityId": str(actor["authority"]), "agentId": str(actor["id"]),
                "contextId": str(binding["context_id"]), "hostId": str(system["host"]),
                "nativeThread": binding["native_thread"]}
    if any(metadata[k] != value for k, value in expected.items()):
        raise Denied("Original host/Writer/context/thread differs")
    ref = "exec:" + str(uuid5(UUID(metadata["contextId"]),
        metadata["nativeTurn"] + ":" + metadata["itemId"] + ":" + metadata["sha256"]))
    if metadata["reference"] != ref:
        raise Conflict("Original native pin reference differs")
    original = c.execute("""SELECT result FROM hb_context_operation
        WHERE agent=%s AND purpose='writer' AND operation=%s""",
        (actor["id"], "context-begin:" + metadata["nativeTurn"])).fetchone()
    scopes = original["result"].get("executionScopes", []) if original else []
    matches = [x for x in scopes if x.get("id") == metadata["scopeId"]]
    if len(matches) != 1 or any(matches[0].get(k) != metadata[k] for k in (
            *expected, "nativeTurn", "materialId", "sourceRequestId", "deliveryId")):
        raise Denied("Accepted original native execution scope unavailable")
    work = c.execute("""SELECT source FROM hb_context_material
        WHERE agent=%s AND id=%s AND kind='work'""", (actor["id"], metadata["materialId"])).fetchone()
    delivery = c.execute("SELECT event FROM hb_delivery WHERE agent=%s AND id=%s",
                         (actor["id"], metadata["deliveryId"])).fetchone()
    if (not work or not delivery or str(work["source"]) != metadata["sourceRequestId"]
            or str(delivery["event"]) != metadata["sourceRequestId"]):
        raise Denied("Original accepted responsibility no longer resolves")
    # Completed Work remains valid historical evidence. This grants no new work,
    # publication, execution, previous role or previous membership authority.
    store.readable_event(c, actor, metadata["sourceRequestId"])
    return metadata


def record(c, store, actor, purpose, binding, reference):
    row = c.execute("""SELECT native_action FROM hb_context_selection
        WHERE agent=%s AND purpose=%s AND reference=%s""",
        (actor["id"], purpose, reference)).fetchone()
    if not row or row["native_action"] is None:
        raise Denied("Original retained native metadata unavailable")
    metadata = authorize(c, store, actor, purpose, binding, row["native_action"])
    return {"reference": reference, "revision": 1, "sha256": digest(metadata),
            "content": {"schema": "lax-retained-native-action-reference/v1", "nativeAction": metadata}}


def candidate(c, store, actor, purpose, binding, p, value):
    from .context_selection import snapshot
    if set(value) != {"selectionRevision", "nativeAction", "reason"}:
        raise ValueError("Exact action-selection preflight fields required")
    if type(value["selectionRevision"]) is not int or value["selectionRevision"] != binding["selection_revision"]:
        raise Conflict("Context selection revision changed")
    if not isinstance(value["reason"], str) or not 1 <= len(value["reason"]) <= 500:
        raise ValueError("Bounded selection reason required")
    metadata = authorize(c, store, actor, purpose, binding, value["nativeAction"])
    # Private savepoint only: no mark/revision adoption until all host/native
    # preflight completes and commit rechecks this exact canonical cut.
    with c.transaction(force_rollback=True):
        c.execute("""INSERT INTO hb_context_selection(agent,purpose,reference,reason,selected_revision,native_action)
            VALUES(%s,%s,%s,%s,1,%s) ON CONFLICT(agent,purpose,reference)
            DO UPDATE SET reason=EXCLUDED.reason,native_action=EXCLUDED.native_action""",
            (actor["id"], purpose, metadata["reference"], value["reason"], Jsonb(metadata)))
        counts = c.execute("""SELECT count(*) AS total,
            count(*) FILTER(WHERE native_action IS NOT NULL) AS actions
            FROM hb_context_selection WHERE agent=%s AND purpose=%s""", (actor["id"], purpose)).fetchone()
        if counts["total"] > 16 or counts["actions"] > 8:
            raise Conflict("Native/optional retained selection count exceeded")
        refreshed = {**binding, "selection_revision": binding["selection_revision"] + 1}
        snap = snapshot(c, store, actor, purpose, refreshed,
            {**p, "value": {"materialIds": [str(x) for x in binding["active_material"]]}}, preflight=True)
    return snap


def prepare(c, store, actor, purpose, binding, p):
    value = p["value"]
    snap = candidate(c, store, actor, purpose, binding, p, value)
    return {"schema": "lax-native-action-selection-preflight/v1",
            "nativeThread": p["nativeThread"], "nativeTurn": p["nativeTurn"],
            "selection": value, "snapshot": snap,
            "meaning": "Metadata-only tentative cut. No selected mark or native history changed. Host must validate whole pins and the jointly bounded native packet before commit."}


def commit(c, store, actor, purpose, binding, p):
    from .context_selection import selected
    value = p["value"]
    if set(value) != {"preflightOperationId", "preflightSha256", "budget"}:
        raise ValueError("Exact original action preflight and host budget receipt required")
    old = c.execute("""SELECT result FROM hb_context_operation
        WHERE agent=%s AND purpose=%s AND operation=%s""",
        (actor["id"], purpose, value["preflightOperationId"])).fetchone()
    original = old["result"] if old else {}
    if (original.get("schema") != "lax-native-action-selection-preflight/v1"
            or original.get("nativeThread") != p["nativeThread"]
            or original.get("nativeTurn") != p["nativeTurn"]
            or original["snapshot"]["sha256"] != value["preflightSha256"]):
        raise Conflict("Original action-selection preflight unavailable or changed")
    snap = candidate(c, store, actor, purpose, binding, p, original["selection"])
    if snap["sha256"] != original["snapshot"]["sha256"]:
        raise Conflict("Canonical role/obligations/selection changed before adoption")
    budget = value["budget"]
    if not isinstance(budget, dict) or set(budget) != set(LIMITS) | {"bundleSha256", "descriptorSha256", "unavailableReferences"}:
        raise ValueError("Exact native packet budget report required")
    for key, maximum in LIMITS.items():
        if type(budget[key]) is not int or not 0 <= budget[key] <= maximum:
            raise Conflict("Native packet " + key + " exceeds joint budget")
    for key in ("bundleSha256", "descriptorSha256"):
        if not isinstance(budget[key], str) or not re.fullmatch("[a-f0-9]{64}", budget[key]):
            raise ValueError("Exact original native packet digest required")
    content = json.loads("".join(x["content"] for x in snap["chunks"]))
    unavailable = budget["unavailableReferences"]
    action_refs = {x["reference"] for x in content["selection"]["marks"] if "native_action" in x}
    if (not isinstance(unavailable,list) or len(unavailable)>8
            or any(not isinstance(x,str) for x in unavailable)
            or len(set(unavailable))!=len(unavailable) or not set(unavailable)<=action_refs
            or original["selection"]["nativeAction"]["reference"] in unavailable):
        raise Conflict("New whole pin must be available; existing unavailable references must be explicit")
    required_unavailable={x["reference"] for x in content["records"]
                          if x.get("access")=="unavailable" and x["reference"] in action_refs}
    if not required_unavailable<=set(unavailable):
        raise Conflict("Revoked original native references must remain unavailable")
    raw_bytes = sum(x["content"]["nativeAction"]["payloadBytes"] for x in content["records"]
                    if x.get("content", {}).get("schema") == "lax-retained-native-action-reference/v1"
                    and x["reference"] not in unavailable)
    expected_items = sum((n + 3071) // 3072 for n in (
        snap["totalBytes"], budget["bundleBytes"], budget["descriptorBytes"]))
    if (budget["rawBytes"] != raw_bytes or budget["bundleBytes"] < raw_bytes
            or budget["decodedBytes"] != snap["totalBytes"] + budget["bundleBytes"] + budget["descriptorBytes"]
            or budget["renderedItems"] != expected_items
            or budget["wireBytes"] < budget["decodedBytes"]):
        raise Conflict("Joint native packet accounting differs")
    chosen = original["selection"]; metadata = chosen["nativeAction"]
    c.execute("""INSERT INTO hb_context_selection(agent,purpose,reference,reason,selected_revision,native_action)
        VALUES(%s,%s,%s,%s,1,%s) ON CONFLICT(agent,purpose,reference)
        DO UPDATE SET reason=EXCLUDED.reason,native_action=EXCLUDED.native_action""",
        (actor["id"], purpose, metadata["reference"], chosen["reason"], Jsonb(metadata)))
    c.execute("""UPDATE hb_purpose_context SET selection_revision=selection_revision+1
        WHERE agent=%s AND purpose=%s""", (actor["id"], purpose))
    marks = selected(c, actor, purpose, {**binding, "selection_revision": binding["selection_revision"] + 1})
    return {**marks, "preflightOperationId": value["preflightOperationId"],
            "snapshotSha256": snap["sha256"], "hostBudget": budget,
            "verification": "Hollywood verified canonical ownership/auth/CAS/cut. Native pin reads and render measurements are explicitly enrolled-host reported, not Hollywood filesystem proof."}
