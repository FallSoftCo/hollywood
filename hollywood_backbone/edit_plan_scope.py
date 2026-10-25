"""Canonical edit metadata fences and immutable receipt helpers."""
from psycopg.types.json import Jsonb

from . import collaboration as claims
from .store import Conflict, Denied, digest, json_value


class EditError(Conflict):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def need(condition, code, message):
    if not condition:
        raise EditError(code, message)


def now(c):
    return c.execute("SELECT clock_timestamp() AS value").fetchone()["value"]


def plan_row(c, authority, identity):
    row = c.execute("SELECT * FROM hb_edit_plan WHERE id=%s AND authority=%s FOR UPDATE",
                    (identity, authority)).fetchone()
    if not row:
        raise Denied("Edit plan unavailable")
    return row


def revision(c, plan):
    return c.execute("SELECT * FROM hb_edit_plan_revision WHERE plan=%s AND revision=%s",
                     (plan["id"], plan["revision"])).fetchone()


def consents(c, plan):
    return c.execute("SELECT * FROM hb_edit_consent WHERE plan=%s AND plan_revision=%s ORDER BY work",
                     (plan["id"], plan["revision"])).fetchall()


def consent_digest(rows):
    return digest(json_value(rows))


def pending(c, plan):
    return c.execute("SELECT * FROM hb_edit_permit WHERE plan=%s AND state<>'settled' ORDER BY created_at",
                     (plan["id"],)).fetchall()


def claims_snapshot(c, plan, manifest, allow_expired=False):
    result = []
    for file in manifest["files"]:
        row = c.execute("SELECT * FROM hb_path_claim WHERE id=%s FOR UPDATE", (file["claimId"],)).fetchone()
        need(row and row["authority"] == plan["authority"] and row["host"] == plan["host"]
             and row["workspace_id"] == plan["workspace_id"] and row["kind"] == "file"
             and row["relative_path"] == file["relativePath"], "scope_mismatch", "Exact physical file claim required")
        owner = c.execute("SELECT owner,delivery FROM hb_work WHERE id=%s", (row["work"],)).fetchone()
        need(owner and owner["owner"] == row["owner"] and owner["delivery"] == row["origin_delivery"],
             "claim_changed", "Original claim owner/attempt needs explicit settlement or handoff")
        need(row["state"] == "active" and row["revision"] == file["claimRevision"]
             and row["content_sha256"] == file["beforeSha256"]
             and row["source_revision"] == manifest["sourceRevision"],
             "claim_changed", "Claim revision/content/source changed")
        need(allow_expired or row["expires_at"] > now(c), "claim_expired", "Expired ownership remains held")
        need(str(row["work"]) in {member["workId"] for member in manifest["participants"]},
             "consent_required", "Claim owner must participate in the plan")
        result.append(row)
    return result


def validate_manifest(store, c, actor, work, goal, source, binding, manifest):
    members = manifest["participants"]
    member_ids = [m["workId"] for m in members]
    need(len(set(member_ids)) == len(member_ids) and manifest["integratorWorkId"] == str(work["id"])
         and str(work["id"]) in member_ids, "invalid_manifest", "Unique participants and current integrator required")
    need(next(m for m in members if m["workId"] == str(work["id"]))["sourceRevision"] == manifest["sourceRevision"],
         "invalid_manifest", "Integrator source revision must match the target")
    owners = set()
    for member in members:
        row = c.execute("SELECT * FROM hb_work WHERE id=%s AND authority=%s", (member["workId"], actor["authority"])).fetchone()
        need(row and row["goal"] == goal["id"] and row["room"] == work["room"],
             "scope_mismatch", "Participants must belong to the same canonical goal and room")
        peer = store.actor(c, row["owner"])
        row, _ = claims.work_scope(store, c, peer, member["workId"])
        need(row["revision"] == member["workRevision"] and peer["id"] not in owners,
             "work_changed", "Participant ownership/revision differs")
        owners.add(peer["id"])
    paths = [claims.canonical_path(f["relativePath"]) for f in manifest["files"]]
    need(len(set(paths)) == len(paths) and len({f["claimId"] for f in manifest["files"]}) == len(paths),
         "invalid_manifest", "Unique file paths and claims required")
    files = {f["relativePath"]: f for f in manifest["files"]}
    slices, seen = [], set()
    for item in manifest["slices"]:
        path = claims.canonical_path(item["relativePath"])
        need(item["sliceId"] not in seen and path in files and item["workId"] in member_ids,
             "invalid_manifest", "Unique slices in declared files/participants required")
        seen.add(item["sliceId"])
        need(item["beforeSha256"] == files[path]["beforeSha256"]
             and 0 <= item["byteStart"] <= item["byteEnd"] <= files[path]["beforeSize"],
             "invalid_manifest", "Slice must bind exact base bytes and range")
        for other in slices:
            if other["relativePath"] == path:
                collision = (max(item["byteStart"], other["byteStart"]) < min(item["byteEnd"], other["byteEnd"])
                             or item["byteStart"] == item["byteEnd"] == other["byteStart"]
                             or other["byteStart"] == other["byteEnd"] and item["byteStart"] <= other["byteStart"] < item["byteEnd"]
                             or item["byteStart"] == item["byteEnd"] and other["byteStart"] <= item["byteStart"] < other["byteEnd"])
                need(not collision, "slice_conflict", "Overlapping base-byte slices require explicit serial revision")
        slices.append(item)
    need(set(paths) == {s["relativePath"] for s in slices}
         and set(member_ids) - {str(work["id"])} <= {s["workId"] for s in slices},
         "invalid_manifest", "Every file and non-integrator participant needs a slice")
    for file in manifest["files"]:
        need((file["beforeSha256"] != "absent" or file["beforeSize"] == 0)
             and (file["afterSha256"] != "absent" or file["afterSize"] == 0),
             "invalid_manifest", "Absent files have zero bytes")
    plan = dict(authority=actor["authority"], host=binding["physical_host"], workspace_id=binding["physical_workspace_id"])
    # UUID values from SQL are compared through canonical JSON in this helper.
    result = claims_snapshot_json(c, plan, manifest)
    return result


def claims_snapshot_json(c, plan, manifest):
    # Normalize a new plan's host to the same UUID representation returned by SQL.
    from uuid import UUID
    return claims_snapshot(c, {**plan, "host": UUID(str(plan["host"]))}, manifest)


def participant_fences(store, c, plan, manifest, rows, cleanup=False):
    expected = {m["workId"]: m for m in manifest["participants"]}
    need({str(r["work"]) for r in rows if r["active"]} == set(expected),
         "consent_required", "Every participant and integrator must consent")
    for vote in rows:
        profile = c.execute("""SELECT 1 FROM hb_collaboration_bridge b JOIN hb_host_admission h
            ON h.host=b.host AND h.controller=b.controller WHERE b.host=%s AND b.controller=%s
            AND b.revision=%s AND b.state='active' AND b.protocol='canonical-edit-plans/v2'""",
            (vote["source"]["hostId"], vote["bridge"], vote["bridge_revision"])).fetchone()
        need(profile, "consent_changed", "Participant bridge enrollment was revoked or changed")
        source = vote["source"]
        from .physical_workspaces import registered
        registered(c, plan["authority"], source, vote["work"], vote["target"], cleanup=cleanup)
        actor = store.actor(c, source["agentId"])
        work, goal = claims.work_scope(store, c, actor, str(vote["work"]), cleanup=cleanup)
        need(goal["id"] == plan["goal"] and work["room"] == plan["room"]
             and (cleanup or work["revision"] == expected[str(vote["work"])]["workRevision"]),
             "work_changed", "Participant canonical scope changed")
        if source["schema"] == "hollywood-native-source/v3":
            from .purpose_effects import binding as purpose_binding
            try:
                accepted = purpose_binding(c, actor, source)
            except (Conflict, Denied) as error:
                raise EditError("participant_unsettled", "Original accepted purpose consent binding changed") from error
            need(str(accepted["workspace_id"]) == str(vote["context_workspace"]),
                 "participant_unsettled", "Participant purpose workspace changed")
            # Consent is durable after its inference ends. Its immutable begin
            # receipt and independently observed logical executor remain required;
            # a later turn/claim or enrollment change invalidates this consent.
            row = c.execute("""SELECT d.*,e.state AS executor_state,e.controller,e.observed_at,
                h.controller AS current_controller,h.sample_max_age FROM hb_delivery d
                JOIN hb_agent_role r ON r.principal=d.agent AND r.revision=%s
                JOIN hb_placement p ON p.agent=d.agent AND p.host=d.host AND p.enabled
                JOIN hb_executor e ON e.id=%s AND e.delivery=d.id
                  AND e.generation=d.generation AND e.claim_id=d.claim_id
                JOIN hb_host_admission h ON h.host=d.host
                WHERE d.id=%s AND d.agent=%s AND d.host=%s""",
                (source["roleRevision"],source["executorId"],source["deliveryId"],
                 actor["id"],source["hostId"])).fetchone()
        else:
            row = c.execute("""SELECT d.*,e.state AS executor_state,e.controller,e.observed_at,h.controller AS current_controller,
                 h.sample_max_age FROM hb_delivery d JOIN hb_agent_execution x ON x.delivery=d.id AND x.generation=d.generation
                 JOIN hb_agent_context a ON a.principal=x.principal AND a.context_id=x.context_id
                   AND a.workspace_id=x.workspace_id AND a.native_thread=x.native_thread AND a.host=d.host
                 JOIN hb_agent_role r ON r.principal=d.agent AND r.revision=x.role_revision
                 JOIN hb_placement p ON p.agent=d.agent AND p.host=d.host AND p.enabled
                 JOIN hb_executor e ON e.delivery=d.id AND e.generation=d.generation AND e.claim_id=d.claim_id
                 JOIN hb_host_admission h ON h.host=d.host
                 WHERE d.id=%s AND d.agent=%s AND d.host=%s AND x.workspace_id=%s
                   AND x.context_id=%s AND x.role_revision=%s""",
                 (source["deliveryId"], actor["id"], source["hostId"], vote["context_workspace"],
                  source["contextId"], source["roleRevision"])).fetchone()
        need(row and not row["cancellation_requested"] and (cleanup or str(work["delivery"]) == source["deliveryId"]),
             "participant_unsettled", "Participant consent lineage changed")
        need(row["claim_id"] == source["claimId"] and row["generation"] == source["generation"]
             and row["native_thread"] == source["nativeThread"] and row["native_turn"] == source["nativeTurn"],
             "participant_unsettled", "Participant native incarnation changed")
        fresh = row["observed_at"] and (now(c) - row["observed_at"]).total_seconds() < row["sample_max_age"]
        need((row["state"] == "running" and row["executor_state"] == "live" and fresh
              and row["controller"] == row["current_controller"])
             or (row["state"] == "completed" and row["executor_state"] == "stopped" and row["observed_at"]),
             "participant_unsettled", "Participant executor is unknown or unsettled")
        claims.settled_controls(c, {"origin_delivery": source["deliveryId"]})
    integrator = next(r for r in rows if r["work"] == plan["integrator_work"])
    peers = [r for r in rows if r["work"] != plan["integrator_work"]]
    need(integrator["peer_digest"] == consent_digest(peers), "consent_changed", "Integrator must consent to exact participant artifact receipts last")


def observed_scope(files, expected):
    need(len(files) == len(expected) and len({f["relativePath"] for f in files}) == len(files),
         "file_set_mismatch", "Atomic observation must cover every file exactly once")
    actual = {claims.canonical_path(f["relativePath"]): (f["sha256"], f["size"]) for f in files}
    need(set(actual) == {f["relativePath"] for f in expected}
         and all(sha != "absent" or size == 0 for sha, size in actual.values()),
         "file_set_mismatch", "Only declared target file observations are allowed")
    return actual


def observed(files, expected, phase):
    actual = observed_scope(files, expected)
    wanted = {f["relativePath"]: (f[phase + "Sha256"], f[phase + "Size"]) for f in expected}
    need(actual == wanted, "content_drift", "Observed file bytes differ from the consented manifest")


def record(store, c, actor, source, payload, result, path, room, native=True, receipt_key="editPlanReceipt", evidence_only=None):
    operation = payload["operationId"]
    result = json_value({**result, "mayApply": False, "newExecutionAdmitted": False})
    event = store.event(c, actor, room, "progress", "Canonical edit metadata recorded",
                        source=source["requestId"], **claims.protocol_audience(c, source),
                        data={"type": "collaborationProtocol", "nativeAction": claims.action_reference(source, operation) if native else None,
                            "evidenceOnly": not native if evidence_only is None else evidence_only, receipt_key: result})
    result["eventId"] = str(event)
    c.execute("UPDATE hb_event SET data=jsonb_set(data,%s,%s) WHERE id=%s", ([receipt_key], Jsonb(result), event))
    wire = {"method": "POST", "path": path, "body": payload}
    c.execute("INSERT INTO hb_command(authority,principal,operation,digest,event,wire_request,accepted_result) VALUES(%s,%s,%s,%s,%s,%s,%s)",
              (actor["authority"], actor["id"], operation, digest(wire), event, Jsonb(wire), Jsonb(result)))
    if native:
        c.execute("INSERT INTO hb_native_effect_cursor(authority,last_seq) VALUES(%s,0) ON CONFLICT DO NOTHING", (actor["authority"],))
        sequence = c.execute("UPDATE hb_native_effect_cursor SET last_seq=last_seq+1 WHERE authority=%s RETURNING last_seq", (actor["authority"],)).fetchone()["last_seq"]
        c.execute("""INSERT INTO hb_native_effect(authority,principal,delivery,generation,fingerprint,source,
            method,path,wire_digest,operation,authority_seq,wire_body_captured)
            VALUES(%s,%s,%s,%s,%s,%s,'POST',%s,%s,%s,%s,true)""",
            (actor["authority"], actor["id"], source["deliveryId"], source["generation"], digest([source, wire]),
             Jsonb(source), path, digest(wire), operation, sequence))
    return result
