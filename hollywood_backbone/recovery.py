"""Explicit manager reconciliation of a known pre-turn admission failure.

Stopped execution alone is not proof that external effects never occurred.
The authorized manager attests that distinction after inspecting the original
receipts. Server checks bind that declaration to this exact failed attempt.
"""
from .store import Conflict


def prepare(store, c, actor, work, payload):
    prior = payload["priorDeliveryId"]
    if work["state"] != "assigned" or str(work["delivery"]) != prior:
        raise Conflict("Recovery requires the exact currently assigned failed attempt")
    if work["report_event"] or work["evidence"]:
        raise Conflict("Reported work requires its ordinary review/rework lifecycle")
    delivery = c.execute(
        "SELECT * FROM hb_delivery WHERE id=%s AND authority=%s FOR SHARE",
        (prior, actor["authority"]),
    ).fetchone()
    if (
        not delivery or delivery["state"] != "failed"
        or delivery["cancellation_requested"] or delivery["native_turn"]
        or str(delivery["result_event"]) != payload["resultEventId"]
    ):
        raise Conflict("Only an explicitly reconciled pre-turn failure can recover")
    result = store.readable_event(c, actor, payload["resultEventId"])
    if (
        result["author"] != delivery["agent"] or result["source"] != delivery["event"]
        or result["data"].get("type") != "executionState"
        or result["data"].get("state") != "failed"
        or result["data"].get("deliveryId") != prior
    ):
        raise Conflict("Original failure receipt does not match the attempt")
    executor = c.execute(
        "SELECT * FROM hb_executor WHERE id=%s AND authority=%s FOR SHARE",
        (payload["executorId"], actor["authority"]),
    ).fetchone()
    if (
        not executor or executor["delivery"] != delivery["id"]
        or executor["agent"] != delivery["agent"]
        or executor["host"] != delivery["host"]
        or executor["generation"] != delivery["generation"]
        or executor["claim_id"] != delivery["claim_id"]
        or executor["state"] != "stopped" or not executor["controller"]
        or not executor["observed_at"] or not executor["evidence"]
    ):
        raise Conflict("An independent stopped receipt for this exact executor is required")
    if c.execute(
        "SELECT 1 FROM hb_native_effect WHERE delivery=%s LIMIT 1", (prior,)
    ).fetchone():
        raise Conflict("Native business effects require separate effect reconciliation")
    if c.execute(
        "SELECT 1 FROM hb_attention WHERE delivery=%s AND state NOT IN ('closed','expired') LIMIT 1",
        (prior,),
    ).fetchone():
        raise Conflict("Original native attention remains unsettled")
    if c.execute("SELECT 1 FROM hb_native_control WHERE delivery=%s LIMIT 1", (prior,)).fetchone():
        raise Conflict("Original native control requires its own reconciliation")
    if not c.execute(
        "SELECT 1 FROM hb_work_attempt WHERE delivery=%s AND work=%s",
        (prior, work["id"]),
    ).fetchone():
        raise Conflict("Original work-attempt lineage is unavailable")
    evidence = {
        "outcome": payload["outcome"], "priorDeliveryId": prior,
        "resultEventId": payload["resultEventId"], "executorId": payload["executorId"],
        "executorRevision": executor["revision"],
        "executorDescriptorDigest": executor["descriptor_digest"],
        "executorController": str(executor["controller"]),
        "executorObservedAt": str(executor["observed_at"]),
        "executorEvidence": executor["evidence"],
        "reconciliationSha256": payload["reconciliationSha256"],
        "note": payload["note"],
        "semantics": "Authorized manager attestation after original receipt inspection; stopped is not proof of no effects.",
        "originalAttemptPreserved": True, "newExecutionAdmitted": False,
    }
    c.execute(
        """UPDATE hb_work SET state='planned',request_event=NULL,delivery=NULL,
           note=%s,revision=revision+1 WHERE id=%s""",
        (payload["note"], work["id"]),
    )
    return evidence
