"""Explicit recovery of a proven provider rejection before model execution.

The authenticated runtime independently observes native history and effects.
The original failed inference stays immutable; this is no model retry trigger.
"""
from .context_work_frontier import resume_interrupted, terminal_permit


def acknowledge(store, c, actor, purpose, binding, p):
    value = p["value"]
    if (set(value) != {"nativeTurn", "status", "errorCode", "parameter", "nativeItemsSha256"}
            or value["status"] != 400 or value["errorCode"] != "unsupported_parameter"
            or value["parameter"] != "tool_choice"
            or p["nativeTurn"] != value["nativeTurn"]
            or not isinstance(value["nativeItemsSha256"], str)
            or len(value["nativeItemsSha256"]) != 64
            or any(x not in "0123456789abcdef" for x in value["nativeItemsSha256"])):
        raise ValueError("Exact original pre-inference unsupported tool-choice rejection required")
    captured = {**p, "value": {"nativeTurn": value["nativeTurn"]}}
    if purpose == "writer":
        result = resume_interrupted(store, c, actor, purpose, binding, captured,
                                    terminal_status="failed")
        eligible = result["continuedMaterialIds"]
    else:
        permit = terminal_permit(c, actor, purpose, binding, captured, terminal_status="failed")
        kind, state = {"reader": ("input", "pending"), "responder": ("need", "ready")}[purpose]
        eligible = []
        for identity in permit["material_ids"]:
            row = c.execute("SELECT * FROM hb_context_material WHERE id=%s AND agent=%s",
                            (identity, actor["id"])).fetchone()
            if not row or row["kind"] != kind or row["state"] != state:
                continue
            event = store.readable_event(c, actor, row["source"])
            observation = purpose == "reader" and row["value"].get("roomObservation") is True
            optional = purpose == "responder" and row["value"].get("required") is False
            store.room(c, actor, event["room"], "can_read" if observation else "can_post")
            delivery = c.execute("SELECT * FROM hb_delivery WHERE agent=%s AND event=%s",
                                 (actor["id"], row["source"])).fetchone()
            if (not delivery and not (observation or optional)
                    or delivery and delivery["cancellation_requested"]):
                continue
            eligible.append(str(identity))
        result = {"permitId": str(permit["id"]), "newExecutionAdmitted": False,
                  "originalTurnRepeated": False, "workCompletionInferred": False}
    return {**result, "schema": "lax-provider-rejection-resolution/v1",
            "contextId": str(binding["context_id"]), "purpose": purpose,
            "nativeThread": p["nativeThread"], "nativeTurn": value["nativeTurn"],
            "nativeItemsSha256": value["nativeItemsSha256"],
            "eligibleMaterialIds": eligible, "providerRejectionAcknowledged": True}
