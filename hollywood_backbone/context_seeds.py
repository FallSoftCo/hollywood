"""Human-owned canonical seed drafts; preview never admits inference or resets a window."""
from uuid import UUID
from psycopg.types.json import Jsonb
from .store import Conflict, Denied, digest, json_value
from . import context_material, context_selection

SCHEMA = """
CREATE TABLE IF NOT EXISTS hb_context_seed_draft(
 id uuid PRIMARY KEY, agent uuid NOT NULL, purpose text NOT NULL,
 reviewer uuid NOT NULL REFERENCES hb_principal(id),
 context_id uuid NOT NULL, native_thread text, generation bigint NOT NULL,
 revision bigint NOT NULL DEFAULT 1 CHECK(revision>0),
 marks jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(agent,purpose) REFERENCES hb_purpose_context(agent,purpose));
CREATE TABLE IF NOT EXISTS hb_context_seed_operation(
 draft uuid NOT NULL REFERENCES hb_context_seed_draft(id),
 actor uuid NOT NULL REFERENCES hb_principal(id), operation text NOT NULL,
 digest text NOT NULL, result jsonb NOT NULL,
 PRIMARY KEY(draft,actor,operation));
INSERT INTO hb_schema VALUES(39) ON CONFLICT DO NOTHING;
"""


class SeedConflict(Conflict):
    code = "CONTEXT_SEED_REVIEW_REQUIRED"

    def __init__(self, details):
        self.details = details
        super().__init__("Seed sources or context changed; review the current revisions")


def authorize(store, c, actor_id, token_digest, agent, purpose):
    actor = store.actor(c, actor_id)
    if actor["kind"] != "human" or not actor["administrator"]:
        raise Denied("Human administrator required for this initial review interface")
    # A repeatable-read snapshot waiting behind revocation must fail, not revive
    # the earlier token. Locking rows changed since that snapshot causes 40001.
    if not c.execute("""SELECT t.digest FROM hb_token t WHERE t.principal=%s
            AND t.digest=%s AND NOT t.revoked FOR SHARE""",
            (actor["id"], token_digest)).fetchone():
        raise Denied("Human credential unavailable")
    target = store.actor(c, agent)
    if target["kind"] != "agent" or target["authority"] != actor["authority"]:
        raise Denied("Same-authority agent required")
    binding = c.execute("""SELECT b.* FROM hb_purpose_context b
        JOIN hb_context_system s ON s.agent=b.agent AND s.enabled
        WHERE b.agent=%s AND b.purpose=%s""", (agent, purpose)).fetchone()
    if not binding:
        raise Denied("Purpose context unavailable")
    return actor, target, binding


def source(c, store, actor, target, purpose, binding, reference):
    if reference.startswith("exec:"):
        raise Denied("Native action review requires its separate whole-item protocol")
    # Existing purpose ceilings apply, including no automatic writer room inbox.
    record = context_selection.material(
        c, store, target, purpose, binding, "human-seed-preview", reference)
    if reference.startswith("event:"):
        store.readable_event(c, actor, reference[6:])
    elif reference.startswith(("goal:", "work:")):
        from .work import goal_row, work_row
        kind, identity = reference.split(":", 1)
        (goal_row if kind == "goal" else work_row)(store, c, actor, identity)
    elif reference != "role":
        row = c.execute("SELECT source FROM hb_context_material WHERE id=%s AND agent=%s",
                        (str(UUID(reference)), target["id"])).fetchone()
        if not row:
            raise Denied("Original material unavailable")
        store.readable_event(c, actor, row["source"])
    return record


def draft_row(c, actor, agent, purpose, identity):
    row = c.execute("""SELECT * FROM hb_context_seed_draft
        WHERE id=%s AND agent=%s AND purpose=%s AND reviewer=%s FOR UPDATE""",
        (identity, agent, purpose, actor["id"])).fetchone()
    if not row:
        raise Denied("Own human seed draft required")
    return row


def same_context(row, binding):
    if (row["context_id"], row["native_thread"], row["generation"]) != (
            binding["context_id"], binding["native_thread"], binding["rebuild_generation"]):
        raise SeedConflict({"reason": "context_generation_changed",
                            "draftGeneration": row["generation"],
                            "currentGeneration": binding["rebuild_generation"]})


def metadata(row):
    return json_value({key: row[key] for key in
        ("id", "agent", "purpose", "reviewer", "context_id", "native_thread",
         "generation", "revision", "marks", "created_at", "updated_at")})


def mutate(store, actor_id, token_digest, agent, purpose, identity, payload, *, create):
    with store.pool.connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        actor, target, binding = authorize(
            store, c, actor_id, token_digest, agent, purpose)
        # Serialize only this draft. Runtime selections and bindings are untouched.
        c.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                  ("context-seed:" + identity,))
        fingerprint = digest({"create": create, "agent": agent, "purpose": purpose,
                              "draft": identity, "payload": payload})
        old = c.execute("""SELECT digest,result FROM hb_context_seed_operation
            WHERE draft=%s AND actor=%s AND operation=%s""",
            (identity, actor["id"], payload["operationId"])).fetchone()
        if old:
            # Receipts contain only selection metadata, never source content.
            draft_row(c, actor, agent, purpose, identity)
            if old["digest"] != fingerprint:
                raise Conflict("Original seed operation changed")
            return old["result"]
        if create:
            if c.execute("SELECT 1 FROM hb_context_seed_draft WHERE id=%s",
                         (identity,)).fetchone():
                raise Conflict("Seed draft already exists; read its original operation")
            record = source(c, store, actor, target, purpose, binding, "role")
            marks = {"role": {"revision": record["revision"], "sha256": record["sha256"],
                             "reason": "Current canonical role is mandatory",
                             "markedBy": str(actor["id"]), "authorKind": "human"}}
            c.execute("""INSERT INTO hb_context_seed_draft
                (id,agent,purpose,reviewer,context_id,native_thread,generation,marks)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s)""",
                (identity, agent, purpose, actor["id"], binding["context_id"],
                 binding["native_thread"], binding["rebuild_generation"], Jsonb(marks)))
        else:
            row = draft_row(c, actor, agent, purpose, identity)
            same_context(row, binding)
            if payload["revision"] != row["revision"]:
                raise Conflict("Seed draft revision changed")
            marks = dict(row["marks"])
            reference = payload["reference"]
            if reference == "role" and not payload["keep"]:
                raise Conflict("Canonical role cannot be removed")
            # Removing a no-longer-authorized pin needs no read of its old content.
            if payload["keep"]:
                record = source(c, store, actor, target, purpose, binding, reference)
                if (payload["sourceRevision"], payload["sourceSha256"]) != (
                        record["revision"], record["sha256"]):
                    raise SeedConflict({"reason": "source_changed", "reference": reference,
                                        "currentRevision": record["revision"],
                                        "currentSha256": record["sha256"]})
                marks[reference] = {"revision": record["revision"], "sha256": record["sha256"],
                                    "reason": payload["reason"], "markedBy": str(actor["id"]),
                                    "authorKind": "human"}
            else:
                marks.pop(reference, None)
            if len(marks) > context_selection.MAX_MARKS + 1:
                raise Conflict("Bounded draft mark limit reached; no pins were removed")
            c.execute("""UPDATE hb_context_seed_draft SET marks=%s,revision=revision+1,
                updated_at=clock_timestamp() WHERE id=%s""", (Jsonb(marks), identity))
        row = draft_row(c, actor, agent, purpose, identity)
        result = {"schema": "lax-human-seed-draft/v1", "draft": metadata(row),
                  "operationId": payload["operationId"], "nativeWindowChanged": False}
        c.execute("INSERT INTO hb_context_seed_operation VALUES(%s,%s,%s,%s,%s)",
                  (identity, actor["id"], payload["operationId"], fingerprint, Jsonb(result)))
        return result


def preview(store, actor_id, token_digest, agent, purpose, identity, operation=None):
    with store.pool.connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        actor, target, binding = authorize(store, c, actor_id, token_digest, agent, purpose)
        row = draft_row(c, actor, agent, purpose, identity)
        if operation is not None:
            receipt = c.execute("""SELECT result FROM hb_context_seed_operation
                WHERE draft=%s AND actor=%s AND operation=%s""",
                (identity, actor["id"], operation)).fetchone()
            if not receipt:
                from .context_system import ContextOperationNotFound
                raise ContextOperationNotFound()
            return receipt["result"]
        same_context(row, binding)
        marks = []
        for reference, pin in sorted(row["marks"].items()):
            record = source(c, store, actor, target, purpose, binding, reference)
            if (pin["revision"], pin["sha256"]) != (record["revision"], record["sha256"]):
                raise SeedConflict({"reason": "source_changed", "reference": reference,
                                    "currentRevision": record["revision"],
                                    "currentSha256": record["sha256"]})
            if reference != "role":
                marks.append({"reference": reference, "reason": pin["reason"],
                              "selected_revision": pin["revision"], "sha256": pin["sha256"],
                              "markedBy": pin["markedBy"], "authorKind": pin["authorKind"]})
        kind = {"reader": "input", "writer": "work", "responder": "need"}[purpose]
        ids = [str(value["id"]) for value in c.execute("""SELECT id
            FROM hb_context_material WHERE agent=%s AND id=ANY(%s::uuid[])
            AND kind=%s AND state<>ALL(%s) ORDER BY id""",
            (agent, binding["active_material"], kind, context_selection.TERMINAL)).fetchall()]
        selected = {"selectionRevision": row["revision"], "marks": marks,
                    "policy": "Explicit human draft; not the agent's runtime selection"}
        try:
            snapshot = context_selection.snapshot(c, store, target, purpose, binding,
                {"nativeThread": binding["native_thread"], "nativeTurn": "human-seed-preview",
                 "value": {"materialIds": ids}}, preflight=True, selection=selected)
        except context_selection.RebuildBudgetExceeded as error:
            raise SeedConflict({"reason": "canonical_budget_exceeded",
                                "totalBytes": error.total_bytes,
                                "limitBytes": context_selection.MAX_MANIFEST_BYTES}) from error
        # Human admin can read target-authorized obligations; no new grant is made.
        return {"schema": "lax-human-seed-preview/v1", "draft": metadata(row),
                "snapshot": snapshot, "canonicalContentSha256": snapshot["sha256"],
                "size": {"canonicalBytes": snapshot["totalBytes"],
                         "canonicalLimitBytes": context_selection.MAX_MANIFEST_BYTES,
                         "assembledNativeBytes": None, "estimatedTokens": None,
                         "authoritativeInputTokens": None,
                         "scope": "Canonical content only; native instructions/tool overhead not assembled"},
                "stage": "canonical-material-preview", "nativeInputReady": False,
                "nativeWindowChanged": False, "modelCalls": 0,
                "meaning": "Not approved, frozen or installed. Recheck sources and native state at reviewed commit."}


def inspect_source(store, actor_id, token_digest, agent, purpose, reference):
    with store.pool.connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        actor, target, binding = authorize(store, c, actor_id, token_digest, agent, purpose)
        return {"schema": "lax-human-seed-source/v1",
                "record": source(c, store, actor, target, purpose, binding, reference)}
