"""Native source checks share the domain mutation's PostgreSQL transaction.

HTTP writers take an authority fence before principal/domain locks. Revocations
and lifecycle changes take it exclusively; ordinary effects take it shared.
No model, shell, network call or wait for execution belongs inside this fence.
"""
import contextlib
import contextvars
import json
import re
from uuid import UUID

from psycopg.types.json import Jsonb

from .store import Conflict, Denied, digest, json_value

HEADER = "X-Hollywood-Native-Source"
_request = contextvars.ContextVar("hollywood_native_request", default=None)

BUSINESS = {
    "post", "workflow", "decision_write", "principal", "create_room",
    "membership", "placement", "role", "host_policy_set", "portfolio_policy_set",
    "cancel", "native_control_create", "communication_set",
    "collaboration_bridge",
}
METADATA = {
    "executor_begin", "executor_reserve", "executor_attach", "executor_observe",
    "host_sample", "connector_observe", "claim", "transition",
    "native_control_pending", "native_control_update", "native_goal_binding",
    "activity_append", "attention_create", "attention_response",
    "attention_acknowledge", "review_dispatch", "supervision_ack",
    "portfolio_dispatch", "agent_execution_binding",
    "collaboration_call",
}
EXCLUSIVE = {
    "principal", "membership", "placement", "role", "host_policy_set",
    "portfolio_policy_set", "cancel", "workflow", "executor_observe",
    "transition", "native_goal_binding",
    "collaboration_call", "collaboration_bridge",
}
FIELDS = {
    "schema", "authorityId", "agentId", "hostId", "deliveryId", "claimId",
    "generation", "requestId", "nativeThread", "nativeTurn", "callId", "tool",
    "argumentsDigest", "roleRevision", "contextId", "nativeGoalId",
}


def source(raw):
    if raw is None:
        return None
    if len(raw.encode()) > 4096:
        raise ValueError("Native source exceeds limit")

    def unique(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError("Duplicate native source field")
            value[key] = item
        return value

    value = json.loads(raw, object_pairs_hook=unique)
    if not isinstance(value, dict) or set(value) != FIELDS:
        raise ValueError("Native source fields differ")
    if value["schema"] not in ("hollywood-native-source/v1", "hollywood-native-source/v2"):
        raise ValueError("Native source schema differs")
    for key in ("authorityId", "agentId", "hostId", "deliveryId", "requestId"):
        value[key] = str(UUID(value[key]))
    if type(value["generation"]) is not int or value["generation"] < 1:
        raise ValueError("Native generation invalid")
    for key in ("claimId", "nativeThread", "nativeTurn", "callId", "tool"):
        if not isinstance(value[key], str) or not 1 <= len(value[key]) <= 256:
            raise ValueError("Native source identity invalid")
    if type(value["roleRevision"]) is not int or value["roleRevision"] < 1:
        raise ValueError("Native role revision invalid")
    if value["schema"] == "hollywood-native-source/v2" and value["nativeGoalId"] is not None:
        raise ValueError("Agent execution cannot assert a synthetic native goal")
    for key in (("contextId",) if value["schema"] == "hollywood-native-source/v2" else ("contextId", "nativeGoalId")):
        if not isinstance(value[key], str) or not 1 <= len(value[key]) <= 256:
            raise ValueError("Native binding identity invalid")
    if not re.fullmatch(r"[a-f0-9]{64}", value["argumentsDigest"]):
        raise ValueError("Native argument digest invalid")
    return value


@contextlib.contextmanager
def transport(actor, token_digest, method, action, path, body, native_source):
    if method not in ("GET", "POST", "PUT", "DELETE", "PATCH"):
        raise ValueError("Unknown transport method")
    token = _request.set({
        "actor": str(actor), "tokenDigest": token_digest, "method": method,
        "action": action, "path": path, "body": body, "source": native_source,
        "entered": False,
    })
    try:
        yield
    finally:
        _request.reset(token)


def enter(c, identity):
    """Called before Store.actor takes any row/domain lock."""
    request = _request.get()
    if request and str(identity) != request["actor"]:
        return  # Nested lookup of a target principal uses the caller's fence.
    if request and request["entered"]:
        return
    principal = c.execute(
        "SELECT * FROM hb_principal WHERE id=%s AND active", (identity,)
    ).fetchone()
    if not principal:
        raise Denied("Principal inactive")
    write = request is None or request["method"] != "GET"
    if write:
        if request and request["action"] not in BUSINESS | METADATA:
            raise Conflict("Mutation transport upgrade required")
        exclusive = request is None or request["action"] in EXCLUSIVE
        function = "pg_advisory_xact_lock" if exclusive else "pg_advisory_xact_lock_shared"
        c.execute("SELECT " + function + "(hashtextextended(%s,0))",
                  ("native-authority:" + str(principal["authority"]),))
    if request:
        # Authentication may have waited behind a revocation. Recheck after the
        # fence, in this transaction, rather than relying on the HTTP preflight.
        principal = c.execute(
            """SELECT p.* FROM hb_principal p JOIN hb_token t ON t.principal=p.id
               WHERE p.id=%s AND p.active AND t.digest=%s AND NOT t.revoked""",
            (identity, request["tokenDigest"]),
        ).fetchone()
        if not principal:
            raise Denied("Credential changed")
        value = request["source"]
        if write and principal["kind"] == "agent" and request["action"] in BUSINESS:
            if value:
                validate(c, principal, value)
            else:
                from .agent_context import communication_allowed
                if not communication_allowed(c, request, principal):
                    raise Denied("Enrolled communication transport or native execution source required")
        elif write and value and principal["kind"] != "agent":
            raise Denied("Only an agent may assert native provenance")
        # Metadata has its own delivery/generation/turn checks and must remain
        # recoverable after a source stops. Its headers confer no effect authority.
        if write and value and request["action"] in BUSINESS:
            wire_digest = digest({
                "method": request["method"], "path": request["path"],
                "body": request["body"],
            })
            body = request["body"]
            operation = body.get("operationId") if isinstance(body, dict) else None
            fingerprint = digest({
                "source": value, "method": request["method"],
                "path": request["path"], "wireDigest": wire_digest,
            })
            # A serial sequence allocated before commit is unsafe for replay:
            # later transactions can commit first. Lock the enterprise's audit
            # counter until commit, as room events already do for their cursor.
            c.execute(
                """INSERT INTO hb_native_effect_cursor(authority,last_seq)
                   VALUES(%s,0) ON CONFLICT(authority) DO NOTHING""",
                (principal["authority"],),
            )
            sequence = c.execute(
                """UPDATE hb_native_effect_cursor SET last_seq=last_seq+1
                   WHERE authority=%s RETURNING last_seq""",
                (principal["authority"],),
            ).fetchone()["last_seq"]
            c.execute(
                """INSERT INTO hb_native_effect
                   (authority,principal,delivery,generation,fingerprint,source,
                    method,path,wire_digest,operation,authority_seq,wire_body_captured)
                   VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,true)
                   ON CONFLICT(principal,fingerprint) DO NOTHING""",
                (principal["authority"], identity, value["deliveryId"],
                 value["generation"], fingerprint, Jsonb(value), request["method"],
                 request["path"], wire_digest, operation, sequence),
            )
        request["entered"] = True


def validate(c, actor, value):
    if (value["agentId"], value["authorityId"]) != (
        str(actor["id"]), str(actor["authority"])
    ):
        raise Denied("Native source belongs to another actor")
    d = c.execute(
        "SELECT * FROM hb_delivery WHERE id=%s AND authority=%s AND agent=%s",
        (value["deliveryId"], actor["authority"], actor["id"]),
    ).fetchone()
    if not d or any([
        d["generation"] != value["generation"], d["claim_id"] != value["claimId"],
        str(d["host"]) != value["hostId"], str(d["event"]) != value["requestId"],
        d["native_thread"] != value["nativeThread"],
        d["native_turn"] != value["nativeTurn"],
    ]):
        raise Conflict("Native source owner changed")
    role = c.execute(
        "SELECT revision FROM hb_agent_role WHERE principal=%s", (actor["id"],)
    ).fetchone()
    if value["schema"] == "hollywood-native-source/v2":
        from .agent_context import execution
        binding = execution(c, d["id"], d["generation"])
        matches = bool(binding and binding["native_thread"] == value["nativeThread"]
                       and binding["context_id"] == value["contextId"]
                       and binding["role_revision"] == value["roleRevision"])
    else:
        from .native_bindings import read
        binding = read(c, d["id"], d["generation"])
        matches = bool(binding and binding["native_thread"] == value["nativeThread"]
                       and binding["native_goal_id"] == value["nativeGoalId"]
                       and binding["context_id"] == value["contextId"])
    if not role or role["revision"] != value["roleRevision"] or not matches:
        raise Conflict("Native role or goal binding changed")
    if d["state"] != "running" or d["cancellation_requested"]:
        raise Conflict("Native source no longer admits effects")
    row = c.execute(
        """SELECT x.id FROM hb_executor x
           JOIN hb_host_admission h ON h.host=x.host AND h.authority=x.authority
           JOIN hb_principal controller ON controller.id=h.controller
           JOIN hb_placement p ON p.agent=x.agent AND p.host=x.host
           WHERE x.delivery=%s AND x.generation=%s AND x.claim_id=%s
             AND x.state='live' AND x.launch_phase='attached'
             AND x.controller=h.controller AND controller.active AND p.enabled""",
        (d["id"], d["generation"], d["claim_id"]),
    ).fetchone()
    if not row:
        raise Conflict("Original executor is not independently live")


def current_participation(c, actor):
    """Use authenticated live transport scope, never reconciliation caller hints."""
    request = _request.get()
    if (not request or not request["entered"] or request["action"] != "workflow"
            or request["actor"] != str(actor["id"]) or not request["source"]):
        raise Denied("Authenticated current native participation required")
    # Validate again at the mutation point under the same authority fence.
    validate(c, actor, request["source"])
    return dict(request["source"])


def listing(store, actor_id, delivery, after=0, limit=100):
    with store.pool.connection() as c:
        actor = store.actor(c, actor_id)
        d = c.execute(
            "SELECT * FROM hb_delivery WHERE id=%s AND authority=%s",
            (delivery, actor["authority"]),
        ).fetchone()
        if not d:
            raise Denied("Delivery unavailable")
        event = store.readable_event(c, actor, d["event"])
        if not actor["administrator"] and actor["id"] not in (d["agent"], event["author"]):
            raise Denied("Native provenance unavailable")
        rows = c.execute(
            """SELECT authority_seq AS sequence,sequence AS identity,source,method,path,
                      wire_digest,operation,wire_body_captured,accepted_at
               FROM hb_native_effect WHERE authority=%s AND delivery=%s
               AND authority_seq>%s ORDER BY authority_seq LIMIT %s""",
            (actor["authority"], delivery, after, limit + 1),
        ).fetchall()
        return json_value({
            "schema": "hollywood-native-effects/v1", "deliveryId": delivery,
            "effects": rows[:limit],
            "nextCursor": rows[limit - 1]["sequence"] if len(rows) > limit else None,
            "meaning": "Accepted Hollywood transactions; not proof external effects stopped or were undone",
        })
