"""Per-work consumed continuation revision; no model starts from a previous hash."""
from psycopg.types.json import Jsonb
from .store import Conflict

def revision(row):
    return row["value"].get("resumeRevision",0)

def runnable(row):
    return revision(row)>row["value"].get("execution",{}).get("resumeRevision",-1)

def normalize(c,actor):
    # Preserve old completed-inference behavior on a software-only upgrade.
    # This records consumption, not work completion or external-effect outcome.
    c.execute("""UPDATE hb_context_material m SET
        value=jsonb_set(value,'{execution,resumeRevision}',
          to_jsonb(coalesce((value->>'resumeRevision')::bigint,0))),
        revision=revision+1,updated_at=clock_timestamp()
        WHERE agent=%s AND kind='work' AND state IN ('pending','running','waiting')
          AND value->'execution'->>'status' IN ('completed','interrupted','failed')
          AND NOT (value->'execution' ? 'resumeRevision')
          AND NOT EXISTS(SELECT 1 FROM hb_purpose_context b WHERE b.agent=m.agent
            AND b.active_turn IS NOT NULL AND m.id=ANY(b.active_material))""",(actor["id"],))


def terminal_permit(c, actor, purpose, binding, p, *, terminal_status):
    """Exact released original inference receipt; grants no execution authority."""
    value = p["value"]
    if (terminal_status not in ("completed", "interrupted", "failed")
            or not isinstance(value.get("nativeTurn"), str)
            or not 1 <= len(value["nativeTurn"]) <= 128):
        raise ValueError("Exact original terminal turn required")
    if binding["active_turn"] is not None:
        raise Conflict("Original native turn is still active")
    original = c.execute("""SELECT result FROM hb_context_operation
        WHERE agent=%s AND purpose=%s AND operation=%s""",
        (actor["id"], purpose, "context-end:" + value["nativeTurn"])).fetchone()
    admission = original["result"].get("admission") or {} if original else {}
    if (not original or original["result"].get("state") != terminal_status
            or original["result"].get("nativeTurn") != value["nativeTurn"]
            or not admission.get("permitId") or not admission.get("capacityReleased")):
        raise Conflict("Known original terminal context-end receipt required")
    system = c.execute("SELECT host FROM hb_context_system WHERE agent=%s AND enabled",
                       (actor["id"],)).fetchone()
    permit = c.execute("""SELECT * FROM hb_context_permit
        WHERE id=%s AND agent=%s AND purpose=%s FOR UPDATE""",
        (admission["permitId"], actor["id"], purpose)).fetchone()
    if (not system or not permit or permit["host"] != system["host"]
            or permit["state"] != "released"
            or permit["terminal_status"] != terminal_status
            or permit["context_id"] != binding["context_id"]
            or permit["native_thread"] != p["nativeThread"]
            or permit["native_turn"] != value["nativeTurn"]):
        raise Conflict("Known original terminal inference receipt required")
    if c.execute("""SELECT 1 FROM hb_context_permit WHERE agent=%s
        AND purpose=%s AND state IN ('reserved','running')""",
        (actor["id"], purpose)).fetchone():
        raise Conflict("Another original inference remains unsettled")
    return permit


def resume_interrupted(store, c, actor, purpose, binding, p, *, terminal_status="interrupted"):
    """One explicit runtime restart may re-enable its known interrupted work.

    The original inference remains terminal. This only updates existing
    continuation metadata; normal admission and original-effect fences remain.
    """
    from .store import Conflict, Denied
    from .context_system import change
    value = p["value"]
    if purpose != "writer":
        raise Denied("Interrupted work continuation belongs to the writer")
    if set(value) != {"nativeTurn"} or not isinstance(value["nativeTurn"], str) or not 1 <= len(value["nativeTurn"]) <= 128:
        raise ValueError("Exact original interrupted turn required")
    permit = terminal_permit(c, actor, purpose, binding, p,
                             terminal_status=terminal_status)
    continued, unchanged = [], []
    for identity in permit["material_ids"]:
        row = c.execute("""SELECT * FROM hb_context_material
            WHERE id=%s AND agent=%s AND kind='work' FOR UPDATE""",
            (identity, actor["id"])).fetchone()
        if not row:
            raise Denied("Original accepted work unavailable")
        event = store.readable_event(c, actor, row["source"])
        store.room(c, actor, event["room"], "can_post")
        delivery = c.execute("""SELECT * FROM hb_delivery
            WHERE event=%s AND agent=%s""", (row["source"], actor["id"])).fetchone()
        execution = row["value"].get("execution", {})
        if (row["state"] not in ("pending", "running") or not delivery
                or delivery["cancellation_requested"]
                or delivery["state"] not in ("claimed", "running")
                or execution.get("nativeThread") != p["nativeThread"]
                or execution.get("nativeTurn") != value["nativeTurn"]
                or execution.get("status") != terminal_status or runnable(row)):
            unchanged.append(str(identity))
            continue
        change(c, row, "pending", {**row["value"],
               "resumeRevision": revision(row) + 1})
        continued.append(str(identity))
    return {"nativeTurn": value["nativeTurn"], "permitId": str(permit["id"]),
            "continuedMaterialIds": continued, "unchangedMaterialIds": unchanged,
            "newExecutionAdmitted": False, "originalTurnRepeated": False,
            "workCompletionInferred": False}


def resume_failed_preturn(store, c, actor, purpose, binding, p):
    """An explicit runtime restart references its original no-model disposition."""
    from .store import Conflict, Denied
    value = p["value"]
    if purpose != "writer":
        raise Denied("Pre-model failed continuation belongs to the writer")
    if (set(value) != {"nativeTurn", "dispositionOperationId"}
            or not isinstance(value["dispositionOperationId"], str)
            or not 1 <= len(value["dispositionOperationId"]) <= 256):
        raise ValueError("Exact original pre-model disposition required")
    original = c.execute("""SELECT result FROM hb_context_operation
        WHERE agent=%s AND purpose='writer' AND operation=%s""",
        (actor["id"], value["dispositionOperationId"])).fetchone()
    result = original["result"] if original else {}
    if (result.get("schema") != "lax-active-context-terminal-uninstalled/v1"
            or result.get("status") != "terminal_uninstalled"
            or result.get("phase") != "preTurn"
            or result.get("failedBeforeModel") is not True
            or result.get("continuationAllowed") is not True
            or result.get("nativeTerminalStatus") != "failed"
            or result.get("nativeThread") != p["nativeThread"]
            or result.get("nativeTurn") != value["nativeTurn"]
            or result.get("contextId") != str(binding["context_id"])):
        raise Conflict("Original authenticated failed-before-model disposition required")
    captured = {**p, "value": {"nativeTurn": value["nativeTurn"]}}
    return {**resume_interrupted(store, c, actor, purpose, binding, captured,
                                 terminal_status="failed"),
            "dispositionOperationId": value["dispositionOperationId"],
            "reason": "confirmed_failed_before_model"}

def resume_unsettled(store, c, actor, purpose, binding, p):
    """Explicit owner recovery of an unfinished Work, never a polling trigger.

    The authenticated runtime independently reads the original native turn and
    reconciles its effects before requesting this once on the existing channel.
    Original inference completion and history remain immutable.
    """
    from .store import Conflict
    value = p["value"]
    if (set(value) != {"nativeTurn", "effectsReconciled", "nativeItemsSha256"}
            or value.get("effectsReconciled") is not True
            or not isinstance(value.get("nativeItemsSha256"), str)
            or len(value["nativeItemsSha256"]) != 64
            or any(x not in "0123456789abcdef" for x in value["nativeItemsSha256"])):
        raise ValueError("Explicit original native observation and reconciled effects required")
    if binding.get("tool_completion") != 1:
        raise Conflict("Paired tool settlement contract required before unfinished Work recovery")
    captured = {**p, "value": {"nativeTurn": value["nativeTurn"]}}
    return {**resume_interrupted(store, c, actor, purpose, binding, captured,
                                 terminal_status="completed"),
            "reason": "explicit_completed_unsettled",
            "nativeItemsSha256": value["nativeItemsSha256"]}
