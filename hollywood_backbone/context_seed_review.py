"""Authenticated human controls for one frozen, idle Writer review."""
import hashlib
import json

from psycopg.types.json import Jsonb
from . import context_seeds as seeds, context_material
from .store import Conflict, Denied, digest, json_value

SCHEMA = """
ALTER TABLE hb_context_seed_draft ADD COLUMN IF NOT EXISTS review jsonb;
ALTER TABLE hb_purpose_context ADD COLUMN IF NOT EXISTS seed_review uuid
 REFERENCES hb_context_seed_draft(id);
ALTER TABLE hb_purpose_context ADD COLUMN IF NOT EXISTS manual_seed_control smallint
 NOT NULL DEFAULT 0 CHECK(manual_seed_control IN (0,1));
INSERT INTO hb_schema VALUES(40) ON CONFLICT DO NOTHING;
"""


def save(c, row, control):
    c.execute("UPDATE hb_context_seed_draft SET review=%s WHERE id=%s",
              (Jsonb(control), row["id"]))


def public(row):
    control = row["review"]
    keys = ("state", "revision", "requestOperationId", "snapshotOperationId",
            "nativeOperationId", "canonicalPreviewSha256", "approvalDigest",
            "preflight", "runtime", "generation", "nativeReceipt", "replacement")
    return {"schema": "lax-human-native-review/v1", "draftId": str(row["id"]),
            "draftRevision": row["revision"], "agentId": str(row["agent"]),
            "purpose": row["purpose"], "reviewerId": str(row["reviewer"]),
            "control": {key: control[key] for key in keys if key in control} if control else None,
            "fullProviderInputReady": False,
            "nextMaterialIds": json.loads("".join(x["content"] for x in control["snapshot"]["chunks"]))["nextMaterialIds"] if control else []}


def human_scope(store, c, actor_id, token_digest, agent, purpose, identity):
    actor = seeds.human(store, c, actor_id, token_digest)
    target = c.execute("SELECT * FROM hb_principal WHERE id=%s AND authority=%s AND kind='agent'",
                       (agent, actor["authority"])).fetchone()
    if not target:
        raise Denied("Same-authority agent required")
    c.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("context:" + agent,))
    binding = c.execute("SELECT * FROM hb_purpose_context WHERE agent=%s AND purpose=%s FOR UPDATE",
                        (agent, purpose)).fetchone()
    if not binding:
        raise Denied("Purpose unavailable")
    row = seeds.draft_row(c, actor, agent, purpose, identity)
    return actor, target, binding, row


def idle(c, binding):
    if (binding["active_turn"] is not None or binding["accepting"] or
            c.execute("""SELECT 1 FROM hb_context_permit WHERE agent=%s AND purpose=%s
                AND state IN ('reserved','running') LIMIT 1""",
                (binding["agent"], binding["purpose"])).fetchone()):
        raise Conflict("Review requires settled native inference and no capacity reservation")


def reauthorize(store, c, row, binding):
    control = row["review"]
    human, target, _ = seeds.authorize(store, c, str(row["reviewer"]),
        control["reviewerCredentialDigest"], str(row["agent"]), row["purpose"])
    seeds.same_context(row, binding)
    idle(c, binding)
    if (str(binding["seed_review"]) != str(row["id"]) or
            binding["rebuild_requested"] != control["requestOperationId"]):
        raise Conflict("Original review no longer owns this purpose")
    content = json.loads("".join(x["content"] for x in control["snapshot"]["chunks"]))
    if binding["selection_revision"] != control["snapshot"]["selectionRevision"]:
        raise Conflict("Runtime selection changed during review")
    for record in content["records"]:
        if seeds.source(c, store, human, target, row["purpose"], binding,
                        record["reference"]) != record:
            raise Conflict("Reviewed canonical source changed")
    for obligation in content["obligations"]:
        current = c.execute("""SELECT m.state,d.cancellation_requested FROM hb_context_material m
            LEFT JOIN hb_delivery d ON d.event=m.source AND d.agent=m.agent
            WHERE m.id=%s AND m.agent=%s""", (obligation["id"], row["agent"])).fetchone()
        if current and current["cancellation_requested"]:
            raise Conflict("Original reviewed obligation was cancelled")
    return content


def freeze(row, binding, view, actor, token_digest):
    request = "human-seed:" + str(row["id"]) + ":" + str(row["revision"])
    key = hashlib.sha256((str(binding["context_id"]) + ":" + request).encode()).hexdigest()
    snapshot = view["snapshot"]
    data = json.loads("".join(x["content"] for x in snapshot["chunks"]))
    data["selection"]["selectionRevision"] = binding["selection_revision"]
    data["humanReview"] = {"draftId": str(row["id"]), "revision": row["revision"],
        "reviewerId": str(actor["id"]), "canonicalPreviewSha256": view["canonicalContentSha256"]}
    encoded = context_material.wire(data)
    from .context_selection import MAX_MANIFEST_BYTES
    if len(encoded.encode()) > MAX_MANIFEST_BYTES:
        raise Conflict("Frozen review exceeds canonical budget; no pins dropped")
    snapshot = {**snapshot, "sha256": digest(data), "totalBytes": len(encoded.encode()),
        "selectionRevision": binding["selection_revision"], "requestOperationId": request,
        "chunks": [{"offset": n, "content": encoded[n:n+3072]}
                   for n in range(0, len(encoded), 3072)]}
    control = {"state": "requested", "revision": 1, "requestOperationId": request,
        "snapshotOperationId": "context-refill-snapshot:" + key,
        "nativeOperationId": "context-refill-native:" + key,
        "canonicalPreviewSha256": view["canonicalContentSha256"], "snapshot": snapshot,
        "reviewerCredentialDigest": token_digest}
    return control


def call(store, actor_id, token_digest, agent, purpose, identity, payload=None, *, content=False):
    with store.pool.connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        actor, target, binding, row = human_scope(store, c, actor_id, token_digest,
                                                 agent, purpose, identity)
        if payload is None:
            if content:
                if not row["review"]:
                    raise Conflict("No frozen review content exists")
                historical = row["review"]["state"] in ("installed", "cancelled", "superseded")
                if historical:
                    captured = json.loads("".join(x["content"] for x in row["review"]["snapshot"]["chunks"]))
                    for record in captured["records"]:
                        # Old content is an audit artifact, not current policy.
                        # Current source access must still hold for both parties.
                        seeds.source(c, store, actor, target, purpose, binding, record["reference"])
                else:
                    reauthorize(store, c, row, binding)
                return {**public(row), "snapshot": row["review"]["snapshot"],
                        "historical": historical}
            return public(row)
        fingerprint = digest({"kind": "native_review", "payload": payload})
        old = c.execute("""SELECT digest,result FROM hb_context_seed_operation
            WHERE draft=%s AND actor=%s AND operation=%s""",
            (identity, actor["id"], payload["operationId"])).fetchone()
        if old:
            if old["digest"] != fingerprint:
                raise Conflict("Original human control operation changed")
            return old["result"]
        action = payload["action"]
        control = row["review"]
        if action == "prepare":
            if (control is not None or payload["revision"] != row["revision"] or
                    payload.get("approvalDigest") is not None):
                raise Conflict("A fresh exact draft revision is required")
            seeds.same_context(row, binding)
            if (purpose != "writer" or binding["manual_seed_control"] != 1
                    or binding["native_action_retention"] != 1 or not binding["native_thread"]):
                raise Denied("Paired manual native Writer profile required")
            if (not target["active"] or not c.execute("""SELECT 1 FROM hb_context_system s
                    JOIN hb_placement l ON l.agent=s.agent AND l.host=s.host AND l.enabled
                    WHERE s.agent=%s AND s.enabled""", (agent,)).fetchone()
                    or binding["rebuild_requested"] or binding["seed_review"]):
                raise Conflict("Original runtime unavailable or another rebuild is pending")
            idle(c, binding)
            view = seeds.build_preview(c, store, actor, target, binding, row, purpose)
            if payload.get("canonicalSha256") != view["canonicalContentSha256"]:
                raise Conflict("Review the exact current canonical preview")
            control = freeze(row, binding, view, actor, token_digest)
            request = control["requestOperationId"]
            c.execute("""UPDATE hb_purpose_context SET seed_review=%s,rebuild_requested=%s
                WHERE agent=%s AND purpose=%s""", (identity, request, agent, purpose))
        else:
            if control is None or payload["revision"] != control["revision"]:
                raise Conflict("Original review revision changed")
            if action != "revise" and payload.get("canonicalSha256") is not None:
                raise ValueError("Canonical preview digest belongs only to prepare")
            if action == "approve":
                if (control["state"] != "prepared" or
                        payload.get("approvalDigest") != control["approvalDigest"]):
                    raise Conflict("Exact native preparation must be reviewed")
                reauthorize(store, c, row, binding)
                control = {**control, "state": "approved", "revision": control["revision"]+1}
            elif action == "revise":
                if control["state"] not in ("requested", "prepared", "approved", "refused", "revise_requested"):
                    raise Conflict("Only an undispatched review can be revised")
                if payload.get("approvalDigest") is not None or not payload.get("replacementDraftId"):
                    raise ValueError("Exact replacement draft required")
                replacement = seeds.draft_row(c, actor, agent, purpose, payload["replacementDraftId"])
                seeds.same_context(replacement, binding)
                if replacement["review"] is not None or replacement["id"] == row["id"]:
                    raise Conflict("Fresh replacement draft required")
                view = seeds.build_preview(c, store, actor, target, binding, replacement, purpose)
                if payload.get("canonicalSha256") != view["canonicalContentSha256"]:
                    raise Conflict("Replacement preview changed")
                control = {**control, "state": "revise_requested", "revision": control["revision"]+1,
                    "replacement": {"draftId": str(replacement["id"]), "revision": replacement["revision"],
                                    "canonicalSha256": view["canonicalContentSha256"]},
                    "replacementCredentialDigest": token_digest}
            elif action == "cancel":
                if control["state"] not in ("requested", "prepared", "approved", "refused", "revise_requested"):
                    raise Conflict("Dispatched/unknown installation must be reconciled, not cancelled")
                if payload.get("approvalDigest") is not None:
                    raise ValueError("Cancellation carries no approval")
                control = {**control, "state": "cancel_requested", "revision": control["revision"]+1}
            else:
                raise ValueError("Unknown human control")
        save(c, row, control)
        result = public({**row, "review": control})
        c.execute("INSERT INTO hb_context_seed_operation VALUES(%s,%s,%s,%s,%s)",
                  (identity, actor["id"], payload["operationId"], fingerprint, Jsonb(result)))
        return result
