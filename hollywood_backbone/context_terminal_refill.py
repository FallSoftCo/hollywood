"""Disposition of a terminal writer refill that never acquired a snapshot.

The trusted LAX runtime reports its original native prefix/terminal observation.
Hollywood independently checks its canonical scope and absence of an accepted
cut. This records no installation, work completion, or new execution authority.
"""
from uuid import UUID

from .context_automatic_refill import authorized, sha, text, uint
from .store import Conflict, Denied

SCHEMA = "lax-active-context-terminal-uninstalled/v1"
FIELDS = {"operationId", "contextId", "permitId", "materialIds", "windowId",
          "historyVersion", "nativeTerminalStatus", "prefixBytes", "prefixSha256"}


def terminal(c, store, actor, purpose, binding, p):
    value = p["value"]
    if purpose != "writer":
        raise Denied("Terminal-before-snapshot recovery currently supports the writer only")
    extra = {"phase", "failedBeforeModel", "failureHistoryBytes", "failureHistorySha256"}
    premodel = set(value) == FIELDS | extra
    if set(value) != FIELDS and not premodel:
        raise ValueError("Exact original terminal refill identity required")
    if premodel:
        if (value["phase"] != "preTurn" or value["failedBeforeModel"] is not True
                or value["nativeTerminalStatus"] != "failed"):
            raise ValueError("Exact original failed pre-model native evidence required")
        uint(value["failureHistoryBytes"], "failureHistoryBytes", positive=True)
        sha(value["failureHistorySha256"])
        if type(value["prefixBytes"]) is not int or value["failureHistoryBytes"] <= value["prefixBytes"]:
            raise ValueError("Original failed native history must extend its prefix")
    for name in ("contextId", "permitId"):
        if str(UUID(text(value[name], name))) != value[name]:
            raise ValueError("Canonical UUID required")
    for name in ("operationId", "windowId"):
        text(value[name], name)
    uint(value["historyVersion"], "historyVersion")
    uint(value["prefixBytes"], "prefixBytes", positive=True)
    sha(value["prefixSha256"])
    ids = value["materialIds"]
    if (not isinstance(ids, list) or len(ids) != 1
            or not isinstance(ids[0], str) or str(UUID(ids[0])) != ids[0]):
        raise ValueError("One exact original writer material required")
    status = value["nativeTerminalStatus"]
    if status not in ("completed", "interrupted", "failed"):
        raise Conflict("Original native turn must be known terminal")
    if (value["contextId"] != str(binding["context_id"])
            or p["nativeThread"] != binding["native_thread"]
            or binding["active_turn"] is not None or binding["accepting"]):
        raise Conflict("Original inactive context/thread required")
    system = authorized(c, actor, binding)
    original = c.execute("""SELECT result FROM hb_context_operation
        WHERE agent=%s AND purpose=%s AND operation=%s""",
        (actor["id"], purpose, "context-end:" + p["nativeTurn"])).fetchone()
    result = original["result"] if original else {}
    admission = result.get("admission") or {}
    if (result.get("nativeTurn") != p["nativeTurn"] or result.get("state") != status
            or admission.get("permitId") != value["permitId"]
            or not admission.get("capacityReleased")):
        raise Conflict("Exact original context-end receipt required")
    permit = c.execute("""SELECT * FROM hb_context_permit WHERE id=%s
        AND agent=%s AND purpose=%s FOR SHARE""",
        (value["permitId"], actor["id"], purpose)).fetchone()
    if (not permit or permit["state"] != "released" or permit["terminal_status"] != status
            or permit["context_id"] != binding["context_id"]
            or permit["host"] != system["host"]
            or permit["native_thread"] != p["nativeThread"]
            or permit["native_turn"] != p["nativeTurn"]
            or [str(identity) for identity in permit["material_ids"]] != ids):
        raise Conflict("Exact released original native permit required")
    if c.execute("""SELECT 1 FROM hb_context_permit WHERE agent=%s AND purpose=%s
        AND state IN ('reserved','running') LIMIT 1""", (actor["id"], purpose)).fetchone():
        raise Conflict("Another original inference remains unsettled")
    row = c.execute("""SELECT * FROM hb_context_material WHERE id=%s
        AND agent=%s AND kind='work' FOR SHARE""", (ids[0], actor["id"])).fetchone()
    execution = row["value"].get("execution", {}) if row else {}
    unfinished = (premodel and row and row["state"] in ("pending", "running")
                  and execution.get("status") == "failed")
    if (not row or not (row["state"] in ("complete", "cancelled") or unfinished)
            or execution.get("nativeThread") != p["nativeThread"]
            or execution.get("nativeTurn") != p["nativeTurn"]):
        raise Conflict("Original captured work needs settlement or confirmed pre-model failure")
    store.readable_event(c, actor, row["source"])
    accepted = c.execute("""SELECT 1 FROM hb_context_operation WHERE agent=%s
        AND purpose=%s AND (operation=%s OR
          (result->>'schema' IN ('lax-active-context-snapshot/v1',
                                'lax-active-context-installed/v1')
           AND (result->>'requestOperationId'=%s OR result->>'operationId'=%s
                OR result->'binding'->>'operationId'=%s))) LIMIT 1""",
        (actor["id"], purpose, *([value["operationId"]] * 4))).fetchone()
    if accepted:
        raise Conflict("Original cut was accepted; use its existing installation recovery")
    return {"schema": SCHEMA, "operationId": value["operationId"],
            "status": "terminal_uninstalled", "nativeTurn": p["nativeTurn"],
            "nativeThread": p["nativeThread"], "nativeTerminalStatus": status,
            "agentId": str(actor["id"]), "purpose": purpose,
            "hostId": str(system["host"]), **value,
            "continuationAllowed": bool(unfinished),
            "newExecutionAdmitted": False, "effectsReplayed": 0,
            "workCompletionInferred": False,
            "verification": "LAX reports an unchanged original native prefix and no exact "
                            "operation intent/installation; Hollywood checked the original "
                            "released permit, terminal receipt, settled material and no accepted cut."}
