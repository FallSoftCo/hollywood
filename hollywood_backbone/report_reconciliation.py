"""Explicit evidence submission after an original execution has completed.

The *current* native participation supplies effect authority. It never becomes
the original executor and never changes the original assignment or binding.
"""

from pathlib import PurePosixPath

from psycopg.types.json import Jsonb

from .store import Conflict, Denied, digest, json_value

ACTION = "work.report_reconcile"
TOOL = "hollywood_work_report_reconcile"


def evidence_paths(evidence):
    seen = set()
    for entry in evidence:
        raw = entry["relativePath"]
        path = PurePosixPath(raw)
        if (not raw or raw == "." or path.is_absolute() or ".." in path.parts
                or "\\" in raw or "\x00" in raw or str(path) != raw or raw in seen):
            raise ValueError("Unique canonical relative workspace paths required")
        seen.add(raw)


def participation(c, actor):
    from .native_effects import current_participation

    value = current_participation(c, actor)
    if value["schema"] != "hollywood-native-source/v2" or value["tool"] != TOOL:
        raise Denied("Explicit persistent-agent reconciliation tool required")
    return value


def fences(store, c, actor, work, goal, payload, source):
    from .agent_context import execution
    from .cooperation import executor, row as policy_row

    if actor["kind"] != "agent" or actor["id"] != work["owner"]:
        raise Denied("Current original work owner required")
    store.room(c, actor, work["room"], "can_post")
    executor(store, c, goal, str(actor["id"]), work["room"])
    # Pausing stops admission, not evidence preservation. Neither terminal
    # cancellation nor accepted goals permit a new report mutation.
    if goal["state"] in ("cancel_requested", "cancelled"):
        raise Conflict("Goal cancellation fences reconciliation")
    if work["state"] in ("cancel_requested", "cancelled"):
        raise Conflict("Work cancellation fences reconciliation")
    if str(work["delivery"]) != payload["priorDeliveryId"]:
        raise Conflict("Original assignment has been superseded")
    prior = c.execute(
        "SELECT * FROM hb_delivery WHERE id=%s AND authority=%s FOR SHARE",
        (payload["priorDeliveryId"], actor["authority"]),
    ).fetchone()
    if (not prior or prior["agent"] != actor["id"] or prior["state"] != "completed"
            or prior["cancellation_requested"] or not prior["native_thread"]
            or not prior["native_turn"] or not prior["result_event"]):
        raise Conflict("Exact original completed non-cancelled execution required")
    if source["deliveryId"] == str(prior["id"]):
        raise Conflict("Later normal participation required")
    original_request = store.readable_event(c, actor, prior["event"])
    result = store.readable_event(c, actor, prior["result_event"])
    if (prior["event"] != work["request_event"]
            or original_request["room"] != work["room"]
            or original_request["recipient"] != actor["id"]
            or original_request["data"].get("workId") != str(work["id"])
            or original_request["data"].get("goalId") != str(goal["id"])
            or result["author"] != actor["id"] or result["source"] != prior["event"]
            or result["data"].get("type") != "executionState"
            or result["data"].get("deliveryId") != str(prior["id"])
            or result["data"].get("generation") != prior["generation"]
            or result["data"].get("state") != "completed"):
        raise Conflict("Original request/result lineage differs")
    attempt = c.execute(
        "SELECT * FROM hb_work_attempt WHERE work=%s ORDER BY number DESC LIMIT 1",
        (work["id"],),
    ).fetchone()
    if (not attempt or attempt["delivery"] != prior["id"]
            or attempt["owner"] != actor["id"] or attempt["request_event"] != prior["event"]
            or attempt["specification"] != work["specification"]
            or attempt["acceptance"] != work["acceptance"]):
        raise Conflict("Latest original attempt differs")
    policy = policy_row(c, goal["id"])
    if policy and original_request["data"].get("cooperationRevision") != policy["revision"]:
        raise Conflict("Captured cooperation requires explicit reconciliation")
    original = execution(c, prior["id"], prior["generation"])
    current = execution(c, source["deliveryId"], source["generation"])
    owner = c.execute(
        "SELECT * FROM hb_agent_context WHERE principal=%s FOR SHARE", (actor["id"],)
    ).fetchone()
    if not original or not current or not owner:
        raise Conflict("Original/current persistent binding required; no inferred migration")
    for binding in (original, current):
        if (binding["principal"] != actor["id"]
                or binding["context_id"] != owner["context_id"]
                or binding["native_thread"] != owner["native_thread"]
                or binding["workspace_id"] != owner["workspace_id"]):
            raise Conflict("Original context/workspace differs")
    if (prior["host"] != owner["host"] or str(owner["host"]) != source["hostId"]
            or prior["native_thread"] != owner["native_thread"]):
        raise Conflict("Original host/thread differs")
    current_request = store.readable_event(c, actor, source["requestId"])
    store.room(c, actor, current_request["room"], "can_post")
    current_goal = current_request["data"].get("goalId")
    if current_goal and c.execute(
        "SELECT 1 FROM hb_goal WHERE id=%s AND state IN ('cancel_requested','cancelled')",
        (current_goal,),
    ).fetchone():
        raise Conflict("Current participation goal is cancelled")
    if c.execute(
        """SELECT 1 FROM hb_delivery WHERE agent=%s AND id NOT IN (%s,%s)
           AND state IN ('claimed','running','unknown','cancel_requested') LIMIT 1""",
        (actor["id"], prior["id"], source["deliveryId"]),
    ).fetchone():
        raise Conflict("Another execution owner remains unsettled")
    resource = c.execute(
        "SELECT * FROM hb_executor WHERE delivery=%s AND generation=%s FOR SHARE",
        (prior["id"], prior["generation"]),
    ).fetchone()
    if (not resource or resource["state"] != "stopped" or not resource["observed_at"]
            or not resource["controller"] or not resource["evidence"]
            or resource["agent"] != actor["id"] or resource["host"] != prior["host"]
            or resource["claim_id"] != prior["claim_id"]):
        raise Conflict("Original executor resource remains unsettled")
    if any(e["hostId"] != str(prior["host"])
           or e["workspaceId"] != str(original["workspace_id"]) for e in payload["evidence"]):
        raise Denied("Evidence differs from original host/workspace")
    evidence_paths(payload["evidence"])
    return prior, attempt, original


def reconcile(store, actor_id, identity, operation, payload, *, wire=None):
    from .memory import current_sequence
    from .work import work_row

    key = digest({"workflow": ACTION, "identity": identity, **payload})
    with store.pool.connection() as c:
        actor = store.actor(c, actor_id)  # Current HTTP source validated under authority fence.
        source = participation(c, actor)
        c.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                  (str(actor["id"]) + ":" + operation,))
        old = c.execute("SELECT * FROM hb_command WHERE principal=%s AND operation=%s",
                        (actor["id"], operation)).fetchone()
        if old and old["digest"] != key:
            raise Conflict("Workflow operation changed")
        work = work_row(store, c, actor, identity)
        goal = c.execute(
            "SELECT * FROM hb_goal WHERE id=%s AND authority=%s FOR UPDATE",
            (work["goal"], actor["authority"]),
        ).fetchone()
        work = work_row(store, c, actor, identity)
        prior, attempt, binding = fences(store, c, actor, work, goal, payload, source)
        if old:
            # Recheck present ownership/rights/cancellation even for receipt reads.
            event = store.readable_event(c, actor, old["event"])
            if not c.execute(
                "SELECT 1 FROM hb_work_report_reconciliation WHERE delivery=%s AND event=%s",
                (prior["id"], old["event"]),
            ).fetchone():
                raise Conflict("Original reconciliation receipt differs")
            return event["data"]["workflowReceipt"]
        if goal["state"] not in ("active", "paused"):
            raise Conflict("Goal does not accept new evidence")
        if work["revision"] != payload["revision"]:
            raise Conflict("Work revision changed")
        if (work["state"] != "assigned" or work["report_event"] or work["evidence"]
                or attempt["report_event"] or attempt["evidence"] or attempt["review_event"]):
            raise Conflict("Only an unreported latest assignment may reconcile")
        latest = current_sequence(c, work["room"], work["goal"])
        head = c.execute("SELECT next_seq FROM hb_authority WHERE id=%s",
                         (actor["authority"],)).fetchone()["next_seq"]
        seen = payload.get("briefingSequence")
        if (seen is not None and (seen < latest or seen > head)) or (latest and seen is None):
            raise Conflict("Read current original-room/work decisions before reconciliation")
        unresolved = {
            "attention": [str(x["id"]) for x in c.execute(
                "SELECT id FROM hb_attention WHERE delivery=%s AND state NOT IN ('closed','expired')",
                (prior["id"],)).fetchall()],
            "controls": [str(x["id"]) for x in c.execute(
                "SELECT id FROM hb_native_control WHERE delivery=%s AND state NOT IN ('native_recorded','rejected')",
                (prior["id"],)).fetchall()],
            "decisions": [str(x["id"]) for x in c.execute(
                """SELECT d.id FROM hb_decision d JOIN hb_decision_revision v
                     ON v.decision=d.id AND v.revision=d.revision
                   WHERE d.room=%s AND (d.goal IS NULL OR d.goal=%s) AND v.state='conflicted'""",
                (work["room"], work["goal"])).fetchall()],
        }
        if payload["state"] == "submitted" and (
            not payload["evidence"] or payload["effectDisposition"] != "reviewed"
            or any(unresolved.values())
        ):
            raise Conflict("Submission needs evidence and explicit effect review without unresolved controls")
        audit = json_value({
            "schema": "completed-work-report-reconciliation/v1",
            "priorDeliveryId": prior["id"], "originalRequestId": prior["event"],
            "originalResultEventId": prior["result_event"], "attemptNumber": attempt["number"],
            "originalGeneration": prior["generation"], "originalContextId": binding["context_id"],
            "originalNativeThread": prior["native_thread"], "originalNativeTurn": prior["native_turn"],
            "hostId": prior["host"], "workspaceId": binding["workspace_id"],
            "currentParticipation": source, "effectDisposition": payload["effectDisposition"],
            "unresolvedEffects": unresolved, "note": payload["note"],
            "evidence": payload["evidence"], "briefingSequence": seen,
            "originalBriefingSequence": attempt["briefing_sequence"],
            "attestation": "Current owner attests effect review; stopped/completed alone proves no effect outcome. Connector hashes bytes on original pinned host/workspace; server verifies scope, not remote filesystem bytes.",
            "newExecutionAdmitted": False, "originalAttemptPreserved": True,
        })
        c.execute(
            """UPDATE hb_work SET state=%s,evidence=%s,note=%s,revision=revision+1
               WHERE id=%s AND revision=%s""",
            (payload["state"], Jsonb(payload["evidence"]), payload["note"], identity, payload["revision"]),
        )
        c.execute("UPDATE hb_goal SET revision=revision+1 WHERE id=%s", (goal["id"],))
        receipt = json_value({
            "accepted": True, "goalId": goal["id"], "goalRevision": goal["revision"] + 1,
            "workId": work["id"], "workRevision": work["revision"] + 1,
            "state": payload["state"], "requestEventId": work["request_event"],
            "deliveryId": prior["id"], "reconciliation": audit,
        })
        event = store.event(
            c, actor, work["room"], "progress", ACTION + ": " + payload["note"],
            source=work["request_event"],
            data={"workflowAction": ACTION, "goalId": str(goal["id"]),
                  "workId": identity, "workflowReceipt": receipt,
                  "workReport": {k: payload.get(k) for k in
                                 ("state", "evidence", "note", "briefingSequence")},
                  "workReportReconciliation": audit},
        )
        receipt["eventId"] = event
        c.execute("UPDATE hb_event SET data=jsonb_set(data,'{workflowReceipt}',%s) WHERE id=%s",
                  (Jsonb(receipt), event))
        c.execute("UPDATE hb_work SET report_event=%s WHERE id=%s", (event, work["id"]))
        c.execute(
            """UPDATE hb_work_attempt SET report_event=%s,evidence=%s,report_note=%s
               WHERE delivery=%s""",
            (event, Jsonb(payload["evidence"]), payload["note"], prior["id"]),
        )
        c.execute(
            """INSERT INTO hb_work_report_reconciliation
               (delivery,authority,work,event,principal,current_delivery,current_generation,evidence)
               VALUES(%s,%s,%s,%s,%s,%s,%s,%s)""",
            (prior["id"], actor["authority"], work["id"], event, actor["id"],
             source["deliveryId"], source["generation"], Jsonb(audit)),
        )
        c.execute(
            """INSERT INTO hb_command(authority,principal,operation,digest,event,wire_request,accepted_result)
               VALUES(%s,%s,%s,%s,%s,%s,%s)""",
            (actor["authority"], actor["id"], operation, key, event, Jsonb(wire), Jsonb(receipt)),
        )
        return receipt
