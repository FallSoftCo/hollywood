"""Canonical physical path claims; no task authority or filesystem is replicated.

A protected, explicitly enrolled host bridge attests actual native callbacks.
This server validates its canonical lifecycle facts; it cannot observe the remote
RPC pipe or bytes itself. No bridge/native restoration is inferred from fixtures.
"""
import json
import unicodedata

from psycopg.types.json import Jsonb

from . import native_effects
from .collaboration_models import CollaborationCall
from .store import Conflict, Denied, digest, json_value

CAPABILITY = "canonical-physical-claims/v2"
TOOLS = {"coordination_act", "apply_patch"}


def canonical_path(value):
    if (not value or len(value.encode()) > 1024 or value.startswith("/")
            or any(part in ("", ".", "..") for part in value.split("/"))
            or "\\" in value or ":" in value or unicodedata.normalize("NFC", value) != value
            or any(unicodedata.category(char).startswith("C") for char in value)):
        raise ValueError("Canonical relative POSIX path required")
    return value


def overlaps(left, right):
    return (left["relative_path"] == right["relative_path"]
            or left["kind"] == "directory" and right["relative_path"].startswith(left["relative_path"] + "/")
            or right["kind"] == "directory" and left["relative_path"].startswith(right["relative_path"] + "/"))


def configure(c, actor, host, payload):
    policy = c.execute("SELECT * FROM hb_host_admission WHERE host=%s AND authority=%s",
                       (host, actor["authority"])).fetchone()
    if not policy or str(policy["controller"]) != payload["controllerId"]:
        raise Denied("Current enrolled host controller required")
    old = c.execute("SELECT * FROM hb_collaboration_bridge WHERE host=%s FOR UPDATE", (host,)).fetchone()
    if (old["revision"] if old else 0) != payload["revision"]:
        raise Conflict("Bridge enrollment revision changed")
    features = payload.get("features", [])
    if old and "canonical-work-resource-routing/v1" in old["features"] and "canonical-work-resource-routing/v1" not in features:
        if c.execute("""SELECT 1 FROM hb_work_resource r JOIN hb_work w ON w.id=r.work
            WHERE r.execution_host=%s AND r.state='selected' AND w.state IN ('planned','assigned','blocked','submitted','rejected','cancel_requested') LIMIT 1""", (host,)).fetchone():
            raise Conflict("Settle selected Work routing before bridge capability downgrade")
    row = c.execute("""INSERT INTO hb_collaboration_bridge
        (authority,host,controller,state,adapter_sha256,enrollment_sha256,protocol,features)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(host) DO UPDATE SET
        controller=EXCLUDED.controller,state=EXCLUDED.state,adapter_sha256=EXCLUDED.adapter_sha256,
        enrollment_sha256=EXCLUDED.enrollment_sha256,protocol=EXCLUDED.protocol,features=EXCLUDED.features,revision=hb_collaboration_bridge.revision+1
        RETURNING *""", (actor["authority"], host, payload["controllerId"], payload["state"],
                          payload["adapterSha256"], payload["enrollmentSha256"], payload["protocol"], Jsonb(features))).fetchone()
    return json_value({"schema": CAPABILITY, "bridge": row,
                       "meaning": "Administrator attests protected bridge installation; not native proof"})


def bridge(c, actor, host, revision=None):
    row = c.execute("""SELECT b.* FROM hb_collaboration_bridge b
        JOIN hb_host_admission h ON h.host=b.host AND h.authority=b.authority AND h.controller=b.controller
        WHERE b.host=%s AND b.authority=%s AND b.state='active'""", (host, actor["authority"])).fetchone()
    if actor["kind"] != "service" or not row or row["controller"] != actor["id"]:
        raise Denied("Separately enrolled protected collaboration bridge required")
    if revision is not None and revision != row["revision"]:
        raise Conflict("Bridge enrollment changed")
    return row


def read_bridge(store, actor_id, host):
    with store.pool.connection() as c:
        actor = store.actor(c, actor_id)
        if not actor["administrator"]:
            bridge(c, actor, host)
        row = c.execute("SELECT * FROM hb_collaboration_bridge WHERE host=%s AND authority=%s",
                        (host, actor["authority"])).fetchone()
        enabled = bool(row and row["state"] == "active" and row["protocol"] == "canonical-edit-plans/v2")
        return json_value({"schema": CAPABILITY, "bridge": row,
            "implemented": ["physical-claims", "receipt-recovery", "edit-plans", "mutation-permits", "stopped-observations"],
            "editPlans": enabled, "patchPermits": enabled, "nativeChildAdmission": False,
            "physicalTargets": enabled, "targetSchema": "hollywood-physical-target/v1",
            "deliveryEdits": enabled, "deliveryEditSchema": "canonical-delivery-edits/v1",
            "workDeliveryEdits": enabled, "workDeliveryEditPolicy": "canonical-work-delivery-edits/v1",
            "workResourceRouting": enabled and "canonical-work-resource-routing/v1" in (row["features"] if row else []),
            "agentResourceGrants": True,
            "resourceRouting": "canonical-agent-resources/v1", "resourceDefaults": True, "resourceMetadata": True, "readOnlyResourceDefaults": True, "agentOriginGoals": True, "deliveryEditReconciliation": True, "scopedGoalOrigins": True,
            "artifactResourceEvidence": True, "artifactRootPolicy": "hollywood-artifact-root/v1",
            "placementControl": "canonical-placement-control/v1", "pausedResourceSetup": True,
            "legacyImplicitContextMutations": False, "activationRequiresProtectedBridgeProof": True})


def participation(store, c, relay, payload, tools=None, patch_actions=None):
    source = native_effects.source(json.dumps(payload["source"]))
    if source["schema"] not in {"hollywood-native-source/v2","hollywood-native-source/v3"} or source["tool"] not in (tools if tools is not None else TOOLS):
        raise Denied("Captured native collaboration callback required")
    if source["tool"] == "apply_patch" and payload["command"]["action"] not in (patch_actions if patch_actions is not None else {"inspect"}):
        raise Denied("Patch preflight cannot silently create ownership or an edit plan")
    bridge(c, relay, source["hostId"], payload["bridgeRevision"])
    actor = store.actor(c, source["agentId"])
    if actor["authority"] != relay["authority"] or actor["kind"] != "agent":
        raise Denied("Native actor unavailable")
    native_effects.validate(c, actor, source, protected_relay=relay)
    resource = c.execute("""SELECT x.* FROM hb_executor x JOIN hb_host_admission h ON h.host=x.host
        WHERE x.id=%s AND x.delivery=%s AND x.generation=%s
        AND x.observed_at > clock_timestamp()-make_interval(secs=>h.sample_max_age)""",
        (payload["executorId"], source["deliveryId"], source["generation"])).fetchone()
    if (not resource or resource["controller"] != relay["id"]
            or resource["state"] != "live" or resource["launch_phase"] != "attached"
            or resource["claim_id"] != source["claimId"] or resource["agent"] != actor["id"]
            or str(resource["host"]) != source["hostId"]
            or resource["descriptor_digest"] != payload["descriptorDigest"]
            or (resource["evidence"] or {}).get("serviceBindingDigest") != payload["serviceBindingDigest"]
            or (resource["evidence"] or {}).get("reason") != "matched-live-incarnation"):
        raise Conflict("Actual protected native executor binding changed")
    binding = c.execute("""SELECT x.* FROM hb_agent_execution x JOIN hb_agent_context a
        ON a.principal=x.principal AND a.context_id=x.context_id AND a.native_thread=x.native_thread
        AND a.workspace_id=x.workspace_id WHERE x.delivery=%s AND x.generation=%s AND a.host=%s""",
        (source["deliveryId"], source["generation"], source["hostId"])).fetchone()
    if source["schema"] == "hollywood-native-source/v3":
        from .purpose_effects import binding as purpose_binding
        binding = purpose_binding(c, actor, source)
    if not binding or binding["principal"] != actor["id"]:
        raise Conflict("Captured host/workspace/context changed")
    event = store.readable_event(c, actor, source["requestId"])
    store.room(c, actor, event["room"], "can_post")
    if event["data"].get("goalId") and c.execute(
        "SELECT 1 FROM hb_goal WHERE id=%s AND state<>'active'", (event["data"]["goalId"],)
    ).fetchone():
        raise Conflict("Current participation goal no longer admits collaboration")
    # Purpose writers admit an exact execution scope and one current inference.
    # Other durable responsibilities (including the waiting human parent) are
    # not competing executions. Physical path claims still fence overlap.
    if source["schema"] != "hollywood-native-source/v3" and c.execute(
        """SELECT 1 FROM hb_delivery WHERE agent=%s AND id<>%s
        AND state IN ('claimed','running','unknown','cancel_requested') LIMIT 1""",
        (actor["id"], source["deliveryId"])).fetchone():
        raise Conflict("Competing original participation remains unsettled")
    return actor, source, binding


def work_scope(store, c, actor, identity, cleanup=False):
    from .cooperation import executor, row as cooperation
    from .work import work_row

    work = work_row(store, c, actor, identity)
    goal = c.execute("SELECT * FROM hb_goal WHERE id=%s FOR UPDATE", (work["goal"],)).fetchone()
    work = work_row(store, c, actor, identity)
    if work["owner"] != actor["id"]:
        raise Denied("Canonical assigned work owner required")
    store.room(c, actor, work["room"], "can_post")
    executor(store, c, goal, str(actor["id"]), work["room"])
    policy = cooperation(c, goal["id"])
    request = store.readable_event(c, actor, work["request_event"]) if work["request_event"] else None
    if policy and (not request or request["data"].get("cooperationRevision") != policy["revision"]):
        raise Conflict("Captured cooperation changed")
    if not cleanup:
        from .goal_contract import bind_work
        bind_work(c,goal,work)
    if not cleanup and (goal["state"] != "active" or work["state"] != "assigned"):
        raise Conflict("Goal/work does not admit editing")
    return work, goal


def decisions(c, work, command):
    from .memory import current_sequence

    latest = current_sequence(c, work["room"], work["goal"])
    head = c.execute("SELECT next_seq FROM hb_authority WHERE id=%s", (work["authority"],)).fetchone()["next_seq"]
    if not latest <= command["briefingSequence"] <= head:
        raise Conflict("Read current sourced decisions before editing")
    if c.execute("""SELECT 1 FROM hb_decision d JOIN hb_decision_revision v
        ON v.decision=d.id AND v.revision=d.revision WHERE d.room=%s
        AND (d.goal IS NULL OR d.goal=%s) AND v.state='conflicted' LIMIT 1""",
        (work["room"], work["goal"])).fetchone():
        raise Conflict("Sourced decisions remain conflicted")


def context_host(c, delivery):
    return c.execute("SELECT host FROM hb_delivery WHERE id=%s", (delivery,)).fetchone()["host"]


def action_reference(source, operation):
    return {**{k: source[k] for k in ("agentId", "deliveryId", "generation", "nativeThread", "nativeTurn", "callId", "tool")},
            "operationId": operation}


def protocol_audience(c, source):
    original = c.execute('SELECT audience,author FROM hb_event WHERE id=%s', (source['requestId'],)).fetchone()
    return {'audience': original['audience'],
            'recipient': original['author'] if original['audience'] == 'direct' else None}


def reviewed(c, claim, command):
    if command["effectDisposition"] != "reviewed":
        raise Conflict("Unknown effects retain path ownership")
    settled_controls(c, claim)


def settled_controls(c, claim):
    for table, terminal in (("hb_attention", "('closed','expired')"),
                            ("hb_native_control", "('native_recorded','rejected')")):
        nonblocking = (" AND NOT (method IN ('hollywood/question','item/tool/requestUserInput')"
                       " AND request->>'isBlocking'='false')") if table=="hb_attention" else ""
        if c.execute(f"SELECT 1 FROM {table} WHERE delivery=%s AND state NOT IN {terminal}"+nonblocking+" LIMIT 1",
                     (claim["origin_delivery"],)).fetchone():
            raise Conflict("Original effects/controls remain unresolved")


def namespace_lock(c, authority, host, workspace):
    namespace = [str(authority), str(host), str(workspace)]
    c.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("paths:" + digest(namespace),))
    return namespace


def lock_conflict(c, namespace, path):
    return c.execute("""SELECT 1 FROM hb_edit_file_lock WHERE authority=%s AND host=%s
        AND workspace_id=%s AND (relative_path=%s OR left(relative_path,length(%s)+1)=%s||'/'
        OR left(%s,length(relative_path)+1)=relative_path||'/') LIMIT 1""",
        (*namespace, path, path, path, path)).fetchone()


def apply(store, c, actor, source, binding, work, goal, command):
    action, identity = command["action"], command["claimId"]
    # Serialize all overlapping paths in the actual physical namespace. Lease
    # expiry never deletes ownership or licenses another native execution.
    namespace = namespace_lock(c, actor["authority"], binding["physical_host"], binding["physical_workspace_id"])
    now = c.execute("SELECT clock_timestamp() AS value").fetchone()["value"]
    claim = c.execute("SELECT * FROM hb_path_claim WHERE id=%s FOR UPDATE", (identity,)).fetchone()
    if action != "inspect":
        path = canonical_path(command["relativePath"]) if action == "acquire" else claim["relative_path"] if claim else ""
        if lock_conflict(c, namespace, path):
            raise Conflict("Unsettled all-file mutation holds claim metadata")
    if action == "acquire":
        if claim:
            raise Conflict("Claim identity already exists; inspect original receipt")
        path = canonical_path(command["relativePath"])
        conflict = c.execute("""SELECT id FROM hb_path_claim WHERE authority=%s AND host=%s
            AND workspace_id=%s AND state<>'released' AND (relative_path=%s
            OR (kind='directory' AND left(%s,length(relative_path)+1)=relative_path||'/')
            OR (%s='directory' AND left(relative_path,length(%s)+1)=%s||'/')) LIMIT 1""",
            (*namespace, path, path, command["kind"], path, path)).fetchone()
        if conflict:
            raise Conflict("Overlapping path ownership remains active or held")
        return c.execute("""INSERT INTO hb_path_claim
            (id,authority,work,owner,host,workspace_id,relative_path,kind,source_revision,content_sha256,
             expires_at,origin_delivery,target) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
             %s+make_interval(secs=>%s),%s,%s) RETURNING *""",
            (identity, actor["authority"], work["id"], actor["id"], binding["physical_host"], binding["physical_workspace_id"],
             path, command["kind"], command["sourceRevision"], command["contentSha256"], now,
             command["leaseSeconds"], source["deliveryId"], Jsonb(binding["physical_target"]))).fetchone()
    if (not claim or str(claim["authority"]) != namespace[0] or str(claim["host"]) != namespace[1]
            or str(claim["workspace_id"]) != namespace[2]):
        raise Denied("Claim differs from actual physical workspace")
    # Inspection reveals the current revision; it must not require knowing it.
    # Scope/owner checks still apply, and every ownership mutation retains CAS.
    if action != "inspect" and claim["revision"] != command["claimRevision"]:
        raise Conflict("Claim revision changed")
    if action == "accept":
        if claim["state"] != "handoff" or claim["target_work"] != work["id"]:
            raise Denied("Explicit matching handoff offer required")
        # Consent cannot survive cancellation/revocation of its original owner
        # unnoticed. Recheck both sides at the ownership transfer's commit.
        origin_actor = store.actor(c, claim["owner"])
        _, origin_goal = work_scope(store, c, origin_actor, str(claim["work"]))
        origin = c.execute("""SELECT d.* FROM hb_delivery d JOIN hb_agent_execution x
            ON x.delivery=d.id AND x.generation=d.generation JOIN hb_agent_role r
            ON r.principal=d.agent AND r.revision=x.role_revision
            JOIN hb_agent_context a ON a.principal=x.principal AND a.context_id=x.context_id
                AND a.workspace_id=x.workspace_id AND a.native_thread=x.native_thread AND a.host=d.host
            JOIN hb_placement p ON p.agent=d.agent AND p.host=d.host AND p.enabled
            JOIN hb_executor e ON e.delivery=d.id AND e.generation=d.generation AND e.claim_id=d.claim_id
            JOIN hb_host_admission h ON h.host=d.host
            WHERE d.id=%s AND d.agent=%s AND d.host=%s AND x.principal=%s
            AND e.controller IS NOT NULL AND e.observed_at IS NOT NULL AND
            ((d.state='running' AND e.state='live' AND e.controller=h.controller AND
              e.observed_at>clock_timestamp()-make_interval(secs=>h.sample_max_age))
             OR (d.state='completed' AND e.state='stopped'))""",
            (claim["origin_delivery"], claim["owner"], context_host(c, claim["origin_delivery"]), claim["owner"])).fetchone()
        if (origin_goal["id"] != goal["id"] or not origin or origin["cancellation_requested"]
                or origin["state"] not in ("running", "completed")):
            raise Conflict("Original handoff consent or effects remain unsettled")
        settled_controls(c, claim)
        claim = c.execute("""UPDATE hb_path_claim SET owner=%s,work=%s,target_work=NULL,state='active',
            revision=revision+1,expires_at=%s+make_interval(secs=>%s),origin_delivery=%s,target=%s WHERE id=%s RETURNING *""",
            (actor["id"], work["id"], now, command["leaseSeconds"], source["deliveryId"], Jsonb(binding["physical_target"]), identity)).fetchone()
    else:
        if claim["owner"] != actor["id"] or claim["work"] != work["id"]:
            raise Denied("Exact canonical claim/work owner required")
        if action == "inspect":
            return claim
        if claim["state"] == "released":
            raise Conflict("Released claim cannot resume")
        if action == "release":
            reviewed(c, claim, command)
            if str(claim["origin_delivery"]) != source["deliveryId"] and not c.execute(
                """SELECT 1 FROM hb_executor WHERE delivery=%s AND state='stopped'
                   AND controller IS NOT NULL AND observed_at IS NOT NULL""", (claim["origin_delivery"],)
            ).fetchone():
                raise Conflict("Original lease executor remains unsettled")
            claim = c.execute("UPDATE hb_path_claim SET state='released',target_work=NULL,revision=revision+1 WHERE id=%s RETURNING *",
                              (identity,)).fetchone()
        elif action == "renew":
            if claim["state"] != "active" or claim["expires_at"] <= now:
                raise Conflict("Expired or handed-off claim remains held; explicitly reconcile")
            claim = c.execute("UPDATE hb_path_claim SET expires_at=%s+make_interval(secs=>%s),revision=revision+1 WHERE id=%s RETURNING *",
                              (now, command["leaseSeconds"], identity)).fetchone()
        elif action == "handoff":
            reviewed(c, claim, command)
            target = c.execute("SELECT * FROM hb_work WHERE id=%s AND authority=%s",
                               (command["targetWorkId"], actor["authority"])).fetchone()
            if not target or target["owner"] == actor["id"] or target["goal"] != goal["id"]:
                raise Denied("Distinct already assigned peer work in same goal required")
            peer = store.actor(c, target["owner"])
            target, _ = work_scope(store, c, peer, str(target["id"]))
            context = c.execute("""SELECT a.* FROM hb_agent_context a JOIN hb_placement p ON p.agent=a.principal
                AND p.host=a.host AND p.enabled WHERE a.principal=%s""", (peer["id"],)).fetchone()
            if target["revision"] != command["targetWorkRevision"] or not context:
                raise Conflict("Current recipient context/revision required")
            from .physical_workspaces import peer_access
            peer_access(c, actor["authority"], {"agentId": str(peer["id"]), "contextId": context["context_id"],
                "hostId": str(context["host"])}, target, claim["workspace_id"])
            claim = c.execute("UPDATE hb_path_claim SET state='handoff',target_work=%s,revision=revision+1 WHERE id=%s RETURNING *",
                              (target["id"], identity)).fetchone()
    return claim


def call(store, actor_id, payload):
    payload = CollaborationCall.model_validate(payload).model_dump(mode="json", by_alias=True)
    command, operation = payload["command"], payload["operationId"]
    with store.pool.connection() as c:
        relay = store.actor(c, actor_id)  # Exclusive authority transport fence.
        actor, source, binding = participation(store, c, relay, payload)
        work, goal = work_scope(store, c, actor, command["workId"],
                                cleanup=command["action"] in ("release", "inspect"))
        if command["action"] != "release" and str(work["delivery"]) != source["deliveryId"]:
            raise Conflict("Editing requires the original current canonical work admission")
        if not command["note"].strip():
            raise ValueError("Specific collaboration note required")
        decisions(c, work, command)
        from .physical_workspaces import attest
        binding = attest(c, relay, source, binding, payload, cleanup=command["action"] in ("release", "inspect"))
        c.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (str(actor["id"]) + ":" + operation,))
        key = digest({"collaboration": payload})
        old = c.execute("SELECT * FROM hb_command WHERE principal=%s AND operation=%s", (actor["id"], operation)).fetchone()
        if old:
            if old["digest"] != key:
                raise Conflict("Original native collaboration operation changed")
            store.readable_event(c, actor, old["event"])
            return old["accepted_result"]
        if work["revision"] != command["workRevision"]:
            raise Conflict("Canonical work revision changed")
        claim = apply(store, c, actor, source, binding, work, goal, command)
        result = json_value({"schema": CAPABILITY, "claim": claim,
            "source": source, "bridgeId": relay["id"], "bridgeRevision": payload["bridgeRevision"],
            "leaseUsableAtReceipt": claim["state"] == "active" and claim["expires_at"] >
                c.execute("SELECT clock_timestamp() AS value").fetchone()["value"],
            "patchPermitIssued": False, "newExecutionAdmitted": False,
            "inspectionIsHistorical": True})
        event = store.event(c, actor, work["room"], "progress", "Canonical path claim: " + command["note"],
                            source=source["requestId"], **protocol_audience(c, source),
                            data={"type": "collaborationProtocol", "nativeAction": action_reference(source, operation), "collaborationReceipt": result})
        result["eventId"] = str(event)
        c.execute("UPDATE hb_event SET data=jsonb_set(data,'{collaborationReceipt}',%s) WHERE id=%s",
                  (Jsonb(result), event))
        wire = {"method": "POST", "path": "collaboration/path-claims", "body": payload}
        c.execute("INSERT INTO hb_command(authority,principal,operation,digest,event,wire_request,accepted_result) VALUES(%s,%s,%s,%s,%s,%s,%s)",
                  (actor["authority"], actor["id"], operation, key, event, Jsonb(wire), Jsonb(result)))
        # Use the existing canonical native-effect ledger, not a second task store.
        c.execute("INSERT INTO hb_native_effect_cursor(authority,last_seq) VALUES(%s,0) ON CONFLICT DO NOTHING", (actor["authority"],))
        sequence = c.execute("UPDATE hb_native_effect_cursor SET last_seq=last_seq+1 WHERE authority=%s RETURNING last_seq", (actor["authority"],)).fetchone()["last_seq"]
        c.execute("""INSERT INTO hb_native_effect(authority,principal,delivery,generation,fingerprint,source,
            method,path,wire_digest,operation,authority_seq,wire_body_captured)
            VALUES(%s,%s,%s,%s,%s,%s,'POST',%s,%s,%s,%s,true)""",
            (actor["authority"], actor["id"], source["deliveryId"], source["generation"], digest([source, wire]),
             Jsonb(source), wire["path"], digest(wire), operation, sequence))
        return result


def read_command(store, actor_id, agent, operation):
    with store.pool.connection() as c:
        relay = store.actor(c, actor_id)
        row = c.execute("SELECT * FROM hb_command WHERE authority=%s AND principal=%s AND operation=%s",
                        (relay["authority"], agent, operation)).fetchone()
        if not row or (row["wire_request"] or {}).get("path") not in (
                "collaboration/path-claims", "collaboration/edit-plans", "collaboration/delivery-edits", "collaboration/mutation-observations"):
            raise Denied("Original collaboration receipt unavailable")
        original = row["wire_request"]["body"]
        observation = row["wire_request"]["path"] == "collaboration/mutation-observations"
        bridge(c, relay, row["accepted_result"]["hostId"] if observation else original["source"]["hostId"])
        if not observation:
            actor = store.actor(c, agent)
            store.readable_event(c, actor, row["event"])
        return json_value({"schema": "hollywood-command/v1", "authorityId": row["authority"],
                           "actorId": row["principal"], "operationId": operation, "eventId": row["event"],
                           "semanticDigest": row["digest"], "wireRequest": row["wire_request"],
                           "wireDigest": digest(row["wire_request"]), "acceptedResult": row["accepted_result"],
                           "evidenceComplete": True, "receiptOnly": True, "newExecutionAdmitted": False})
