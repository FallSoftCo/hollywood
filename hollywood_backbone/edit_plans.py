"""Canonical consent and atomic one-shot file mutation admission.

Only the original successful begin response carries a transient apply grant.
Receipts, retries, timeouts and observations never grant or replay a mutation.
Remote bytes/channel facts remain attestations by the protected enrolled bridge.
"""
import json
from datetime import timedelta

from psycopg.types.json import Jsonb

from . import collaboration as claims
from .edit_plan_models import CAPABILITY, EditPlanCall, MutationObservation
from .collaboration_outcomes import RecordedRejection, original_result, record_rejection
from .edit_plan_scope import (EditError, claims_snapshot, consent_digest, consents, need, now,
                              observed, observed_scope, participant_fences, pending, plan_row, record, revision,
                              validate_manifest)
from .store import Conflict, Denied, digest, json_value

PATH = "collaboration/edit-plans"
OBSERVATIONS = "collaboration/mutation-observations"
PATCH_ACTIONS = {"preflight", "begin", "settle"}


def snapshot(c, plan, store):
    version = revision(c, plan)
    votes = consents(c, plan)
    permits = c.execute("SELECT * FROM hb_edit_permit WHERE plan=%s ORDER BY created_at", (plan["id"],)).fetchall()
    projected = []
    for permit in permits:
        effective = permit["state"]
        if effective != "settled":
            try:
                need(effective != "held" and permit["expires_at"] > now(c), "permit_held", "Held or expired mutation")
                active_fences(store, c, plan, version, permit)
            except (Conflict, Denied):
                effective = "held"
        projected.append({**permit, "effectiveState": effective})
    return json_value({"schema": CAPABILITY, "plan": plan, "manifest": version["manifest"],
        "manifestSha256": version["manifest_digest"], "consents": votes,
        "consentSetDigest": consent_digest(votes),
        "participantConsentDigest": consent_digest([v for v in votes if v["work"] != plan["integrator_work"]]),
        "permits": projected,
        "mayApply": False, "newExecutionAdmitted": False})


def authorize_read(store, c, actor, plan):
    if actor["kind"] == "service":
        policy = c.execute("SELECT host FROM hb_collaboration_bridge WHERE controller=%s AND authority=%s AND state='active'",
                           (actor["id"], actor["authority"])).fetchall()
        hosts = {p["host"] for p in policy}
        version = revision(c, plan)
        members = [p["workId"] for p in version["manifest"]["participants"]]
        member_hosts = c.execute("""SELECT host FROM hb_agent_context
            WHERE principal IN (SELECT owner FROM hb_work WHERE id=ANY(%s::uuid[]))
            UNION SELECT host FROM hb_context_system WHERE enabled
            AND agent IN (SELECT owner FROM hb_work WHERE id=ANY(%s::uuid[]))""",
            (members,members)).fetchall()
        need(any(p["host"] in hosts for p in member_hosts) or plan["host"] in hosts,
             "scope_mismatch", "Controller does not own a participant namespace")
        for host in hosts:
            claims.bridge(c, actor, str(host))
    else:
        store.room(c, actor, plan["room"])


def read(store, actor_id, identity):
    with store.pool.connection() as c:
        actor = store.actor(c, actor_id)
        plan = plan_row(c, actor["authority"], identity)
        authorize_read(store, c, actor, plan)
        return snapshot(c, plan, store)


def original_command(c, actor, payload, path):
    operation = payload["operationId"]
    c.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (str(actor["id"]) + ":" + operation,))
    old = c.execute("SELECT * FROM hb_command WHERE principal=%s AND operation=%s", (actor["id"], operation)).fetchone()
    if old:
        need(old["digest"] == digest({"method": "POST", "path": path, "body": payload}),
             "operation_changed", "Original operation contents changed")
    return old


def check_artifacts(items, slices):
    need(len(items) == len(slices) and len({a["sliceId"] for a in items}) == len(items),
         "artifact_mismatch", "Exact slice artifacts required")
    by_slice = {s["sliceId"]: s for s in slices}
    for item in items:
        claims.canonical_path(item["relativePath"])
        need(item["sliceId"] in by_slice and item["sha256"] == by_slice[item["sliceId"]]["artifactSha256"]
             and item["size"] == by_slice[item["sliceId"]]["artifactSize"],
             "artifact_mismatch", "Artifact bytes do not match consented slice")


def consent(store, c, plan, version, relay, actor, source, binding, work, command, bridge_revision):
    manifest = version["manifest"]
    members = {p["workId"]: p for p in manifest["participants"]}
    need(str(work["id"]) in members and command["manifestSha256"] == version["manifest_digest"]
         and command["sourceRevision"] == members[str(work["id"])]["sourceRevision"],
         "scope_mismatch", "Consent must bind exact participant/source/manifest")
    if binding["physical_host"] == plan["host"] and binding["physical_workspace_id"] == plan["workspace_id"]:
        need(command["sourceRevision"] == manifest["sourceRevision"], "source_changed", "Same physical namespace must share the actual source revision")
    old = c.execute("SELECT * FROM hb_edit_consent WHERE plan=%s AND plan_revision=%s AND work=%s FOR UPDATE",
                    (plan["id"], plan["revision"], work["id"])).fetchone()
    need((old["revision"] if old else 0) == command["consentRevision"], "stale_revision", "Consent revision changed")
    cleanup = command["settlementDigest"] is not None
    outstanding = pending(c, plan)
    if cleanup:
        need(len(outstanding) == 1 and outstanding[0]["state"] == "held" and outstanding[0]["observation"]
             and digest(outstanding[0]["observation"]) == command["settlementDigest"],
             "observation_changed", "Fresh consent must name the exact stopped-executor observation")
        if old:
            need(old["host"] == binding["physical_host"] and old["workspace_id"] == binding["physical_workspace_id"],
                 "scope_mismatch", "Settlement consent cannot move a participant namespace")
    else:
        need(plan["state"] in ("collecting", "ready"), "plan_closed", "Revise a settled/withdrawn plan before consent")
        need(work["revision"] == members[str(work["id"])]["workRevision"], "work_changed", "Participant work changed")
    own_slices = [s for s in manifest["slices"] if s["workId"] == str(work["id"])]
    check_artifacts(command["artifacts"], own_slices)
    votes = consents(c, plan)
    if work["id"] == plan["integrator_work"]:
        peers = [v for v in votes if v["work"] != work["id"]]
        need({str(v["work"]) for v in peers if v["active"]} == set(members) - {str(work["id"])}
             and all(v["settlement_digest"] == command["settlementDigest"] for v in peers)
             and command["participantConsentDigest"] == consent_digest(peers),
             "consent_required", "Integrator must explicitly approve exact participant receipts last")
        foreign_work = {str(v["work"]) for v in peers if v["host"] != plan["host"] or v["workspace_id"] != plan["workspace_id"]}
        check_artifacts(command["imports"], [s for s in manifest["slices"] if s["workId"] in foreign_work])
        need(binding["physical_host"] == plan["host"] and binding["physical_workspace_id"] == plan["workspace_id"],
             "scope_mismatch", "Integrator imports must be observed in the target namespace")
    else:
        need(not command["imports"] and command["participantConsentDigest"] is None,
             "scope_mismatch", "Only the integrator may attest imports/aggregate consent")
    c.execute("""INSERT INTO hb_edit_consent(plan,plan_revision,work,revision,active,source,host,workspace_id,
        artifacts,imports,bridge,bridge_revision,peer_digest,settlement_digest,context_workspace,target) VALUES(%s,%s,%s,1,true,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT(plan,plan_revision,work) DO UPDATE SET revision=hb_edit_consent.revision+1,active=true,
        source=EXCLUDED.source,host=EXCLUDED.host,workspace_id=EXCLUDED.workspace_id,artifacts=EXCLUDED.artifacts,
        imports=EXCLUDED.imports,bridge=EXCLUDED.bridge,bridge_revision=EXCLUDED.bridge_revision,
        peer_digest=EXCLUDED.peer_digest,settlement_digest=EXCLUDED.settlement_digest,
        context_workspace=EXCLUDED.context_workspace,target=EXCLUDED.target""",
        (plan["id"], plan["revision"], work["id"], Jsonb(source), binding["physical_host"], binding["physical_workspace_id"],
         Jsonb(command["artifacts"]), Jsonb(command["imports"]), relay["id"], bridge_revision,
         command["participantConsentDigest"], command["settlementDigest"], binding["workspace_id"], Jsonb(binding["physical_target"])))
    # Updating consent after reservation invalidates that admission, never its locks.
    if outstanding and not cleanup:
        c.execute("UPDATE hb_edit_permit SET state='held',revision=revision+1 WHERE plan=%s AND state IN ('reserved','executing')", (plan["id"],))
    votes = consents(c, plan)
    ready = {str(v["work"]) for v in votes if v["active"]} == set(members)
    if ready:
        integrator = next(v for v in votes if v["work"] == plan["integrator_work"])
        ready = integrator["peer_digest"] == consent_digest([v for v in votes if v["work"] != plan["integrator_work"]])
    c.execute("UPDATE hb_edit_plan SET state=%s WHERE id=%s", ("ready" if ready else "collecting", plan["id"]))


def active_fences(store, c, plan, version, permit=None, cleanup=False):
    votes = consents(c, plan)
    participant_fences(store, c, plan, version["manifest"], votes, cleanup=cleanup)
    if permit and not cleanup:
        need(consent_digest(votes) == permit["consent_digest"], "consent_changed", "Consent changed after preflight")
    claims_snapshot(c, plan, version["manifest"], allow_expired=cleanup)
    return votes


def permit_row(c, plan, command):
    row = c.execute("SELECT * FROM hb_edit_permit WHERE id=%s AND plan=%s FOR UPDATE",
                    (command["permitId"], plan["id"])).fetchone()
    need(row and row["plan_revision"] == plan["revision"] and row["revision"] == command["permitRevision"],
         "stale_revision", "Exact original permit revision required")
    return row


def finish(c, plan, manifest, permit, outcome, evidence):
    # Preserve physical ownership and advance its explicit content baseline; no
    # work report, task completion or new execution follows from mutation facts.
    for file in manifest["files"]:
        content = file["afterSha256"] if outcome == "applied" else file["beforeSha256"]
        c.execute("UPDATE hb_path_claim SET content_sha256=%s,revision=revision+1 WHERE id=%s",
                  (content, file["claimId"]))
    row = c.execute("UPDATE hb_edit_permit SET state='settled',revision=revision+1,settlement=%s WHERE id=%s RETURNING *",
                    (Jsonb(evidence), permit["id"])).fetchone()
    c.execute("DELETE FROM hb_edit_file_lock WHERE permit=%s", (permit["id"],))
    c.execute("UPDATE hb_edit_plan SET state='settled' WHERE id=%s", (plan["id"],))
    return row


def mutate(store, c, relay, actor, source, binding, work, goal, payload):
    command = payload["command"]
    action = command["action"]
    claims.namespace_lock(c, actor["authority"], binding["physical_host"], binding["physical_workspace_id"])
    admission = None
    if action == "propose":
        need(not c.execute("SELECT 1 FROM hb_edit_plan WHERE id=%s", (command["planId"],)).fetchone(),
             "plan_exists", "Plan identity already exists")
        validate_manifest(store, c, actor, work, goal, source, binding, command["manifest"])
        plan = c.execute("""INSERT INTO hb_edit_plan(id,authority,goal,room,integrator_work,host,workspace_id)
            VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING *""", (command["planId"], actor["authority"], goal["id"],
            work["room"], work["id"], binding["physical_host"], binding["physical_workspace_id"])).fetchone()
        c.execute("INSERT INTO hb_edit_plan_revision VALUES(%s,1,%s,%s,%s,clock_timestamp())",
                  (plan["id"], Jsonb(command["manifest"]), digest(command["manifest"]), Jsonb(source)))
        return {"plan": plan, "manifestSha256": digest(command["manifest"])}, None
    plan = plan_row(c, actor["authority"], command["planId"])
    need(plan["revision"] == command["planRevision"] and work["goal"] == plan["goal"] and work["room"] == plan["room"],
         "stale_revision", "Canonical plan revision/scope changed")
    version = revision(c, plan)
    manifest = version["manifest"]
    need(str(work["id"]) in {p["workId"] for p in manifest["participants"]}, "scope_mismatch", "Explicit participant work required")
    if action == "consent":
        consent(store, c, plan, version, relay, actor, source, binding, work, command, payload["bridgeRevision"])
    elif action == "revoke":
        row = c.execute("""UPDATE hb_edit_consent SET active=false,revision=revision+1,source=%s
            WHERE plan=%s AND plan_revision=%s AND work=%s AND revision=%s RETURNING *""",
            (Jsonb(source), plan["id"], plan["revision"], work["id"], command["consentRevision"])).fetchone()
        need(row, "stale_revision", "Consent revision changed")
        c.execute("UPDATE hb_edit_plan SET state='collecting' WHERE id=%s", (plan["id"],))
        c.execute("UPDATE hb_edit_permit SET state='held',revision=revision+1 WHERE plan=%s AND state IN ('reserved','executing')", (plan["id"],))
    else:
        need(work["id"] == plan["integrator_work"] and binding["physical_host"] == plan["host"]
             and binding["physical_workspace_id"] == plan["workspace_id"], "scope_mismatch", "Exact target integrator namespace required")
        if action in ("revise", "withdraw"):
            need(not pending(c, plan), "permit_held", "Settle original mutation before revising/withdrawing")
            if action == "withdraw":
                c.execute("UPDATE hb_edit_plan SET state='withdrawn' WHERE id=%s", (plan["id"],))
            else:
                validate_manifest(store, c, actor, work, goal, source, binding, command["manifest"])
                c.execute("UPDATE hb_edit_plan SET revision=revision+1,state='collecting' WHERE id=%s", (plan["id"],))
                c.execute("INSERT INTO hb_edit_plan_revision VALUES(%s,%s,%s,%s,%s,clock_timestamp())",
                          (plan["id"], plan["revision"] + 1, Jsonb(command["manifest"]), digest(command["manifest"]), Jsonb(source)))
        elif action == "preflight":
            need(plan["state"] == "ready" and not pending(c, plan), "permit_held", "Ready consent and no unsettled permit required")
            votes = active_fences(store, c, plan, version)
            need(all(v["settlement_digest"] is None for v in votes)
                 and consent_digest(votes) == command["consentSetDigest"]
                 and version["manifest_digest"] == command["manifestSha256"]
                 and manifest["patchSha256"] == command["patchSha256"]
                 and manifest["sourceRevision"] == command["sourceRevision"], "consent_changed", "Exact source/manifest/patch/consents required")
            observed(command["filesObserved"], manifest["files"], "before")
            snapshots = claims_snapshot(c, plan, manifest)
            expiry = min(now(c) + timedelta(seconds=command["leaseSeconds"]), *(r["expires_at"] for r in snapshots))
            need(not c.execute("SELECT 1 FROM hb_edit_permit WHERE id=%s", (command["permitId"],)).fetchone(),
                 "permit_exists", "Permit identity cannot be retried or restarted")
            for file in manifest["files"]:
                need(not claims.lock_conflict(c, [str(plan["authority"]), str(plan["host"]), str(plan["workspace_id"])], file["relativePath"]),
                     "scope_busy", "Another unsettled all-file mutation owns this path")
            proof = {key: payload[key] for key in ("executorId", "descriptorDigest", "serviceBindingDigest")}
            proof.update(hostId=str(binding["physical_host"]), workspaceId=str(binding["physical_workspace_id"]),
                         target=payload["target"], contextWorkspaceId=str(binding["workspace_id"]),
                         manifestSha256=version["manifest_digest"], patchSha256=manifest["patchSha256"], claims=json_value(snapshots))
            permit = c.execute("""INSERT INTO hb_edit_permit(id,authority,plan,plan_revision,state,expires_at,
                source,bridge,bridge_revision,proof,consent_digest) VALUES(%s,%s,%s,%s,'reserved',%s,%s,%s,%s,%s,%s) RETURNING *""",
                (command["permitId"], plan["authority"], plan["id"], plan["revision"], expiry, Jsonb(source),
                 relay["id"], payload["bridgeRevision"], Jsonb(proof), consent_digest(votes))).fetchone()
            for file in manifest["files"]:
                c.execute("INSERT INTO hb_edit_file_lock VALUES(%s,%s,%s,%s,%s)",
                          (plan["authority"], plan["host"], plan["workspace_id"], file["relativePath"], permit["id"]))
            return {"planId": plan["id"], "permit": permit}, None
        else:
            permit = permit_row(c, plan, command)
            if action in ("begin", "settle"):
                need(permit["source"] == source, "source_changed", "Only the exact original patch callback may begin/settle")
                need(manifest["patchSha256"] == command["patchSha256"], "patch_changed", "Original patch digest changed")
            if action == "begin":
                need(permit["state"] == "reserved" and permit["expires_at"] > now(c), "permit_expired", "Only a live unconsumed reservation may begin")
                need(permit["bridge"] == relay["id"] and permit["bridge_revision"] == payload["bridgeRevision"],
                     "source_changed", "Protected bridge enrollment changed")
                active_fences(store, c, plan, version, permit)
                need(manifest["sourceRevision"] == command["sourceRevision"], "source_changed", "Target source revision drifted")
                observed(command["filesObserved"], manifest["files"], "before")
                permit = c.execute("UPDATE hb_edit_permit SET state='executing',revision=revision+1 WHERE id=%s RETURNING *", (permit["id"],)).fetchone()
                admission = json_value({"mayApply": True, "permitId": permit["id"], "permitRevision": permit["revision"],
                    "expiresAt": permit["expires_at"], "sourceDigest": digest(source), "patchSha256": manifest["patchSha256"],
                    "files": manifest["files"], "oneShotOriginalResponseOnly": True})
            elif action == "settle":
                need(permit["state"] in ("executing", "held"), "permit_held", "No executing original mutation to settle")
                observed_scope(command["filesObserved"], manifest["files"])
                evidence = {"outcome": command["outcome"], "filesObserved": command["filesObserved"],
                            "sourceRevision": command["sourceRevision"], "source": source}
                reasons = []
                try:
                    need(permit["state"] == "executing" and permit["expires_at"] > now(c), "permit_expired", "Mutation ownership expired or was revoked")
                    need(permit["bridge_revision"] == payload["bridgeRevision"] and permit["bridge"] == relay["id"],
                         "source_changed", "Bridge changed during mutation")
                    active_fences(store, c, plan, version, permit)
                    need(manifest["sourceRevision"] == command["sourceRevision"], "source_changed", "Source changed during mutation")
                    need(command["outcome"] != "unknown", "unknown_effect", "Unknown mutation outcome remains held")
                    observed(command["filesObserved"], manifest["files"], "after" if command["outcome"] == "applied" else "before")
                except (Conflict, Denied) as error:
                    reasons.append(getattr(error, "code", "authority_changed"))
                if reasons:
                    evidence["holds"] = reasons
                    permit = c.execute("UPDATE hb_edit_permit SET state='held',revision=revision+1,settlement=%s WHERE id=%s RETURNING *",
                                       (Jsonb(evidence), permit["id"])).fetchone()
                else:
                    permit = finish(c, plan, manifest, permit, command["outcome"], evidence)
            elif action == "reconcile":
                need(permit["state"] == "held" and permit["observation"]
                     and digest(permit["observation"]) == command["observationDigest"],
                     "observation_changed", "Exact stopped-executor observation required")
                votes = active_fences(store, c, plan, version, cleanup=True)
                need(all(v["settlement_digest"] == command["observationDigest"] for v in votes),
                     "consent_required", "Fresh participant/integrator settlement consent required")
                need(permit["observation"]["outcome"] == command["outcome"], "unknown_effect", "Observation outcome differs")
                observed(permit["observation"]["filesObserved"], manifest["files"], "after" if command["outcome"] == "applied" else "before")
                permit = finish(c, plan, manifest, permit, command["outcome"], {
                    "outcome": command["outcome"], "observationDigest": command["observationDigest"],
                    "source": source, "effectDisposition": "reviewed", "reconciliationOnly": True})
            return {"planId": plan["id"], "permit": permit}, admission
    plan = plan_row(c, actor["authority"], command["planId"])
    return {"plan": plan, "manifestSha256": revision(c, plan)["manifest_digest"],
            "consentSetDigest": consent_digest(consents(c, plan))}, None


def call(store, actor_id, payload):
    payload = EditPlanCall.model_validate(payload).model_dump(mode="json", by_alias=True)
    need(len(json.dumps(payload).encode()) <= 16000, "request_too_large", "Bounded edit metadata exceeds limit")
    command = payload["command"]
    with store.pool.connection() as c:
        relay = store.actor(c, actor_id)
        actor, source, binding = claims.participation(store, c, relay, payload,
            tools={"apply_patch"} if command["action"] in PATCH_ACTIONS else {"collaborative_edit_plan"},
            patch_actions=PATCH_ACTIONS)
        profile = claims.bridge(c, relay, source["hostId"], payload["bridgeRevision"])
        need(profile["protocol"] == CAPABILITY, "capability_missing", "Explicit edit-plan bridge profile required")
        cleanup = command["action"] in ("revoke", "withdraw", "reconcile") or command["action"] == "consent" and command["settlementDigest"] is not None
        work, goal = claims.work_scope(store, c, actor, command["workId"], cleanup=cleanup)
        need(cleanup or str(work["delivery"]) == source["deliveryId"], "source_changed", "Current original owned work admission required")
        claims.decisions(c, work, command)
        from .physical_workspaces import attest
        binding = attest(c, relay, source, binding, payload, cleanup=cleanup)
        need(command["note"].strip(), "invalid_request", "Specific note required")
        old = original_command(c, actor, payload, PATH)
        if old:
            store.readable_event(c, actor, old["event"])
            return {"receipt": original_result(old["accepted_result"]), "admission": None, "replayed": True}
        need(work["revision"] == command["workRevision"], "work_changed", "Current work revision required")
        rejected = None
        try:
            with c.transaction():
                result, admission = mutate(store, c, relay, actor, source, binding, work, goal, payload)
        except EditError as error:
            wire = {"method": "POST", "path": PATH, "body": payload}
            rejected = record_rejection(store, c, actor, source, payload,
                PATH, work["room"], error, digest(wire))
        else:
            receipt = record(store, c, actor, source, payload, {"schema": CAPABILITY, **result}, PATH, work["room"])
    if rejected is not None:
        raise RecordedRejection(rejected)
    return {"receipt": receipt, "admission": admission, "replayed": False}


def observe(store, actor_id, payload):
    payload = MutationObservation.model_validate(payload).model_dump(mode="json", by_alias=True)
    with store.pool.connection() as c:
        relay = store.actor(c, actor_id)
        permit = c.execute("SELECT * FROM hb_edit_permit WHERE id=%s AND authority=%s FOR UPDATE",
                           (payload["permitId"], relay["authority"])).fetchone()
        if not permit:
            raise Denied("Original mutation unavailable")
        if permit["scope_kind"] == "delivery":
            from .delivery_edits import observe_locked
            return observe_locked(store, c, relay, payload, permit)
        source = permit["source"]
        profile = claims.bridge(c, relay, source["hostId"], payload["bridgeRevision"])
        need(profile["protocol"] == CAPABILITY, "capability_missing", "Edit-plan observation profile required")
        old = original_command(c, relay, payload, OBSERVATIONS)
        if old:
            return {"receipt": original_result(old["accepted_result"]), "admission": None, "replayed": True}
        need(permit["state"] != "settled" and permit["revision"] == payload["permitRevision"],
             "stale_revision", "Exact unsettled permit required")
        resource = c.execute("SELECT * FROM hb_executor WHERE id=%s AND delivery=%s AND generation=%s AND claim_id=%s",
            (payload["executorId"], source["deliveryId"], source["generation"], source["claimId"])).fetchone()
        need(resource and resource["state"] == "stopped" and resource["observed_at"] and resource["controller"]
             and (resource["evidence"] or {}).get("reason") in (
                 "registered-unit-drained", "registered-invocation-settled", "machine-boot-changed")
             and payload["executorId"] == permit["proof"]["executorId"]
             and resource["descriptor_digest"] == payload["descriptorDigest"] == permit["proof"]["descriptorDigest"]
             and (resource["evidence"] or {}).get("serviceBindingDigest") == payload["serviceBindingDigest"] == permit["proof"]["serviceBindingDigest"],
             "executor_unsettled", "Original independently stopped executor/service binding required")
        plan = plan_row(c, relay["authority"], str(permit["plan"]))
        from .physical_workspaces import attest
        target_binding = attest(c, relay, source, {}, payload, work_id=str(plan["integrator_work"]), cleanup=True)
        need(target_binding["physical_workspace_id"] == plan["workspace_id"] and target_binding["physical_host"] == plan["host"],
             "scope_mismatch", "Observation must resolve original physical target")
        files = revision(c, plan)["manifest"]["files"]
        observed_scope(payload["filesObserved"], files)
        observation = {"outcome": payload["outcome"], "filesObserved": payload["filesObserved"],
            "controllerId": str(relay["id"]), "executorId": payload["executorId"],
            "observedAt": str(now(c)), "operationId": payload["operationId"], "note": payload["note"]}
        permit = c.execute("UPDATE hb_edit_permit SET state='held',revision=revision+1,observation=%s WHERE id=%s RETURNING *",
                           (Jsonb(observation), permit["id"])).fetchone()
        receipt = record(store, c, relay, source, payload, {"schema": CAPABILITY, "permit": permit,
            "observationDigest": digest(observation), "hostId": source["hostId"], "evidenceOnly": True},
            OBSERVATIONS, plan["room"], native=False)
        return {"receipt": receipt, "admission": None, "replayed": False}
