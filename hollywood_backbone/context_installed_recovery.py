"""Acknowledge a verified original installed window after its inference ended.

This is provenance recovery, never active installation, read-fence restoration,
permission restoration, work completion or admission of another inference.
"""
import hashlib
from uuid import UUID
from .context_automatic_refill import (
    INSTALLED, INSTALLED_SCHEMA, SNAPSHOT_SCHEMA, authorized, decoded,
    installed_result, lookup, sha, text, uint,
)
from .store import Conflict


def acknowledge(c, store, actor, purpose, binding, p):
    v = p["value"]
    if set(v) != INSTALLED | {"nativeTerminalStatus"}:
        raise ValueError("Exact terminal original installed cut required")
    for name in ("contextId", "permitId"):
        if str(UUID(text(v[name], name))) != v[name]:
            raise ValueError("Canonical UUID required")
    for name in ("operationId", "snapshotOperationId", "previousWindowId", "windowId",
                 "snapshotReadPoint"):
        text(v[name], name)
    for name in ("snapshotSha256", "nativeActionsSha256", "nativeHistorySha256"):
        sha(v[name])
    for name in ("selectionRevision", "totalBytes", "nativeHistoryBytes"):
        uint(v[name], name, positive=name != "selectionRevision")
    if (p["operationId"] != "active-installed:" + hashlib.sha256(v["operationId"].encode()).hexdigest()
            or v["operationId"] != v["snapshotOperationId"]
            or v["contextId"] != str(binding["context_id"])
            or p["nativeThread"] != binding["native_thread"]
            or binding["active_turn"] is not None or binding["accepting"]
            or binding["rebuild_requested"] is not None or binding["seed_review"] is not None):
        raise Conflict("Exact original inactive installed context required")
    status = v["nativeTerminalStatus"]
    if status not in ("completed", "interrupted", "failed"):
        raise Conflict("Original inference must be known terminal")
    system = authorized(c, actor, binding)
    row = c.execute("""SELECT result FROM hb_context_operation WHERE agent=%s AND purpose=%s
        AND operation=%s""", (actor["id"], purpose, v["snapshotOperationId"])).fetchone()
    cut = row["result"] if row else {}
    captured = cut.get("binding", {})
    if (cut.get("schema") != SNAPSHOT_SCHEMA
            or cut.get("snapshotOperationId") != v["operationId"]
            or captured.get("agentId") != str(actor["id"]) or captured.get("purpose") != purpose
            or captured.get("contextId") != v["contextId"]
            or captured.get("hostId") != str(system["host"])
            or captured.get("nativeThread") != p["nativeThread"]
            or captured.get("nativeTurn") != p["nativeTurn"]
            or captured.get("permitId") != v["permitId"]
            or captured.get("materialIds") != v["materialIds"]):
        raise Conflict("Exact original accepted snapshot binding required")
    # Reauthorize the immutable captured content. It need not still be the
    # current role/material revision: no current availability proofs are restored.
    lookup(c, store, actor, purpose, binding, cut)
    decoded(cut)
    if (v["snapshotReadPoint"] != cut["snapshotReadPoint"]
            or v["snapshotSha256"] != cut["sha256"]
            or v["selectionRevision"] != cut["selectionRevision"]
            or v["totalBytes"] != cut["totalBytes"]
            or v["previousWindowId"] != cut["windowId"]
            or v["windowId"] == v["previousWindowId"]):
        raise Conflict("Original installed cut/digest/window differs")
    end = c.execute("""SELECT result FROM hb_context_operation WHERE agent=%s AND purpose=%s
        AND operation=%s""", (actor["id"], purpose, "context-end:" + p["nativeTurn"])).fetchone()
    ended = end["result"] if end else {}
    admission = ended.get("admission") or {}
    if (ended.get("nativeTurn") != p["nativeTurn"] or ended.get("state") != status
            or admission.get("permitId") != v["permitId"]
            or admission.get("capacityReleased") is not True):
        raise Conflict("Exact original terminal context-end receipt required")
    permit = c.execute("""SELECT * FROM hb_context_permit WHERE id=%s AND agent=%s
        AND purpose=%s FOR SHARE""", (v["permitId"], actor["id"], purpose)).fetchone()
    if (not permit or permit["state"] != "released" or permit["terminal_status"] != status
            or str(permit["context_id"]) != v["contextId"] or permit["host"] != system["host"]
            or permit["native_thread"] != p["nativeThread"] or permit["native_turn"] != p["nativeTurn"]
            or [str(x) for x in permit["material_ids"]] != captured["permitMaterialIds"]):
        raise Conflict("Exact original released inference permit required")
    later = c.execute("""SELECT 1 FROM hb_context_permit WHERE agent=%s AND purpose=%s
        AND (state IN ('reserved','running') OR ticket>%s) LIMIT 1""",
        (actor["id"], purpose, permit["ticket"])).fetchone()
    if later:
        raise Conflict("Another inference supersedes the original installed cut")
    if c.execute("""SELECT 1 FROM hb_context_operation WHERE agent=%s AND purpose=%s
        AND result->>'schema'=%s AND result->>'snapshotOperationId'=%s LIMIT 1""",
        (actor["id"], purpose, INSTALLED_SCHEMA, v["snapshotOperationId"])).fetchone():
        raise Conflict("Original installation already recorded; observe its receipt")
    return {**installed_result(v, p, cut), "recoveredAfterTerminal": True,
            "nativeTerminalStatus": status, "availabilityRestored": False,
            "newExecutionAdmitted": False, "workCompletionInferred": False}
