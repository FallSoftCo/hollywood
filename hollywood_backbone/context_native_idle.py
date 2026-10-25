"""Versioned idle native-window receipts, not inference or execution authority.

The enrolled host independently observes its own native file. Hollywood checks
current purpose/source scope and records the reported metadata in the existing
context operation transaction. No payload, checkpoint turn or work ledger is added.
"""
import json
from uuid import UUID

from .context_automatic_refill import sha, text, uint
from .store import Conflict, Denied

SCHEMA = "lax-native-idle-context-installed/v1"
NATIVE = {"threadId", "turnId", "operationId", "contextId", "purpose",
          "snapshotOperationId", "snapshotReadPoint", "snapshotSha256",
          "nativeActionsSha256", "selectionRevision", "totalBytes",
          "previousWindowId", "windowId", "nativeHistoryBytes", "nativeHistorySha256"}
PREFLIGHT = {"state", "operationId", "packetSha256", "replacementSha256",
             "prefixItemCount", "replacementItemCount", "replacementWireBytes"}
STATE = {"threadId", "contextId", "purpose", "windowId", "historyVersion",
         "lastStartedTurnId", "activeTurnId", "idle", "heldOperationId"}


def installed(c, store, actor, purpose, binding, p, *, selection=None):
    from .context_selection import material, selected

    value = p["value"]
    if set(value) != {"schema", "snapshotOperationId", "nativeReceipt", "preflight"}:
        raise ValueError("Exact versioned idle native receipt required")
    if value["schema"] != SCHEMA:
        raise ValueError("Unknown native idle receipt version")
    if purpose != "writer" or binding.get("native_action_retention", 0) != 1:
        raise Denied("Explicit paired whole-native Writer contract required")
    if binding["active_turn"] is not None or binding["accepting"]:
        raise Conflict("Original inference remains active")
    if c.execute("""SELECT 1 FROM hb_context_permit WHERE agent=%s AND purpose=%s
        AND state IN ('reserved','running') LIMIT 1""", (actor["id"], purpose)).fetchone():
        raise Conflict("Original inference reservation remains unsettled")
    native = value["nativeReceipt"]
    preflight = value["preflight"]
    if (not isinstance(native, dict) or set(native) != NATIVE
            or not isinstance(preflight, dict) or set(preflight) != PREFLIGHT
            or not isinstance(preflight.get("state"), dict)
            or set(preflight["state"]) != STATE):
        raise ValueError("Exact native receipt and preflight fields required")
    state = preflight["state"]
    for name in ("operationId", "turnId", "threadId", "contextId",
                 "snapshotOperationId", "snapshotReadPoint"):
        text(native[name], name)
    text(value["snapshotOperationId"], "Hollywood snapshot operation")
    for name in ("snapshotSha256", "nativeActionsSha256", "nativeHistorySha256"):
        sha(native[name])
    for name in ("packetSha256", "replacementSha256"):
        sha(preflight[name])
    for name in ("selectionRevision", "totalBytes", "nativeHistoryBytes"):
        uint(native[name], name, positive=name != "selectionRevision")
    uint(state["historyVersion"], "historyVersion")
    for name in ("prefixItemCount", "replacementItemCount", "replacementWireBytes"):
        uint(preflight[name], name, positive=True)
    if (not 0 < preflight["prefixItemCount"] < preflight["replacementItemCount"] <= 256
            or preflight["replacementWireBytes"] > 1048576
            or native["totalBytes"] > 98304
            or native["nativeHistoryBytes"] > 256 * 1024**2):
        raise Conflict("Native context receipt exceeds negotiated bounds")
    for name in ("previousWindowId", "windowId"):
        text(native[name], name)
        identity = UUID(native[name])
        if str(identity) != native[name] or identity.version != 7:
            raise ValueError("Canonical native v7 window identity required")
    if (native["previousWindowId"] == native["windowId"]
            or native["threadId"] != p["nativeThread"]
            or native["threadId"] != binding["native_thread"]
            or native["turnId"] != p["nativeTurn"]
            or native["contextId"] != str(binding["context_id"])
            or native["purpose"] != purpose
            or native["operationId"] != native["snapshotOperationId"]
            or preflight["operationId"] != native["operationId"]
            or state["threadId"] != native["threadId"]
            or state["contextId"] != native["contextId"]
            or state["purpose"] != purpose
            or state["windowId"] != native["previousWindowId"]
            or state["lastStartedTurnId"] != native["turnId"]
            or state["activeTurnId"] is not None or state["idle"] is not True
            or state["heldOperationId"] is not None):
        raise Conflict("Original idle native cut or purpose differs")
    original = c.execute("""SELECT result FROM hb_context_operation
        WHERE agent=%s AND purpose=%s AND operation=%s""",
        (actor["id"], purpose, value["snapshotOperationId"])).fetchone()
    snapshot = original["result"] if original else {}
    if (not binding["rebuild_requested"]
            or snapshot.get("schema") != "lax-context-rebuild-snapshot/v1"
            or snapshot.get("nativeThread") != native["threadId"]
            or snapshot.get("contextId") != native["contextId"]
            or snapshot.get("sha256") != native["snapshotSha256"]
            or snapshot.get("snapshotReadPoint") != native["snapshotReadPoint"]
            or snapshot.get("totalBytes") != native["totalBytes"]
            or snapshot.get("selectionRevision") != native["selectionRevision"]
            or native["selectionRevision"] != binding["selection_revision"]
            or snapshot.get("requestOperationId") != binding["rebuild_requested"]):
        raise Conflict("Original Hollywood snapshot or rebuild request changed")
    content = json.loads("".join(chunk["content"] for chunk in snapshot["chunks"]))
    if content["selection"] != (selected(c, actor, purpose, binding) if selection is None else selection):
        raise Conflict("Original selected inputs changed")
    # Re-authorize each delivered source. No old role or membership is restored.
    # Previously unavailable optional pointers convey no content or permission.
    for record in content["records"]:
        try:
            current = material(c, store, actor, purpose, binding, p["nativeTurn"],
                               record["reference"])
        except Denied:
            if record.get("access") == "unavailable":
                continue
            raise
        if record.get("access") != "unavailable" and current != record:
            raise Conflict("Original selected source revision changed")
    c.execute("""UPDATE hb_purpose_context SET rebuild_requested=NULL,
        rebuild_generation=rebuild_generation+1, reads='{}'
        WHERE agent=%s AND purpose=%s""", (actor["id"], purpose))
    return {"schema": SCHEMA, "status": "recorded",
            "contextId": native["contextId"], "nativeThread": native["threadId"],
            "generation": binding["rebuild_generation"] + 1,
            "snapshotOperationId": value["snapshotOperationId"],
            "nativeReceipt": native, "preflight": preflight,
            "verification": "Enrolled LAX runtime reported independent native file verification; "
                            "Hollywood records authenticated provenance, not host filesystem attestation."}
