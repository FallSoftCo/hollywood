"""Business authority for an accepted purpose-native writer turn.

The scope is an immutable context-begin receipt, not a synthetic legacy binding.
Process liveness still comes only from the enrolled independent host controller.
"""
from .store import Conflict, Denied


def validate(c, actor, value, request, *, protected_relay=None):
    b=c.execute("""SELECT b.*,s.host,s.role_revision,s.enabled FROM hb_purpose_context b
        JOIN hb_context_system s ON s.agent=b.agent WHERE b.agent=%s AND b.purpose='writer'
        """,(actor["id"],)).fetchone()
    if protected_relay is None:
        if not request or not b or b["token_digest"] != request["tokenDigest"]:
            raise Denied("Authenticated purpose writer credential required")
    else:
        from .collaboration import bridge
        bridge(c, protected_relay, value["hostId"])
        if (not request or request["actor"] != str(protected_relay["id"])
                or request["action"] not in {"collaboration_call","edit_plan_call","delivery_edit_call"}):
            raise Denied("Authenticated protected collaboration relay required")
    if (not b or not b["enabled"] or not b["accepting"]
            or b["active_turn"]!=value["nativeTurn"] or b["native_thread"]!=value["nativeThread"]
            or str(b["context_id"])!=value["contextId"]):
        raise Denied("Current accepted purpose writer required")
    begin=c.execute("""SELECT result FROM hb_context_operation
        WHERE agent=%s AND purpose='writer' AND operation=%s""",
        (actor["id"],"context-begin:"+value["nativeTurn"])).fetchone()
    scopes=begin["result"].get("executionScopes",[]) if begin else []
    match=[s for s in scopes if s["id"]==value["executionScopeId"]]
    if len(match)!=1:
        raise Denied("Original accepted execution scope required")
    s=match[0]
    expected={"authorityId":value["authorityId"],"agentId":value["agentId"],
        "hostId":value["hostId"],"deliveryId":value["deliveryId"],"claimId":value["claimId"],
        "generation":value["generation"],"sourceRequestId":value["requestId"],
        "contextId":value["contextId"],"nativeThread":value["nativeThread"],
        "nativeTurn":value["nativeTurn"],"roleRevision":value["roleRevision"],
        "executorId":value["executorId"],"purpose":"writer","schema":"lax-execution-scope/v2"}
    if any(s.get(k)!=v for k,v in expected.items()):
        raise Conflict("Original purpose execution scope changed")
    if not c.execute("""SELECT 1 FROM hb_context_permit WHERE agent=%s AND purpose='writer'
        AND native_thread=%s AND native_turn=%s AND state='running' AND role_revision=%s""",
        (actor["id"],value["nativeThread"],value["nativeTurn"],value["roleRevision"])).fetchone():
        raise Conflict("Current inference permit required")
    return True


def accepted_scope(c, delivery):
    """Read the exact current writer admission; metadata grants no effect right."""
    b=c.execute("""SELECT * FROM hb_purpose_context WHERE agent=%s
        AND purpose='writer' AND accepting AND active_turn=%s AND native_thread=%s""",
        (delivery["agent"],delivery["native_turn"],delivery["native_thread"])).fetchone()
    if not b:
        return None
    row=c.execute("""SELECT result FROM hb_context_operation WHERE agent=%s
        AND purpose='writer' AND operation=%s""",
        (delivery["agent"],"context-begin:"+delivery["native_turn"])).fetchone()
    matches=[s for s in row["result"].get("executionScopes",[]) if
        s["deliveryId"]==str(delivery["id"]) and s["generation"]==delivery["generation"]
        and s["claimId"]==delivery["claim_id"]] if row else []
    if len(matches)!=1:
        raise Conflict("One original purpose execution scope required")
    return matches[0]


def binding(c, actor, source):
    """Project the real accepted purpose binding, never write a legacy row."""
    row=c.execute("""SELECT b.*,s.host,s.workspace,s.role_revision,s.enabled
        FROM hb_purpose_context b JOIN hb_context_system s ON s.agent=b.agent
        WHERE b.agent=%s AND b.purpose='writer'""",(actor["id"],)).fetchone()
    begin=c.execute("""SELECT result FROM hb_context_operation WHERE agent=%s
        AND purpose='writer' AND operation=%s""",
        (actor["id"],"context-begin:"+source["nativeTurn"])).fetchone()
    matches=[s for s in begin["result"].get("executionScopes",[]) if
        s["id"]==source["executionScopeId"]] if begin else []
    if len(matches)!=1 or not row:
        raise Denied("Original accepted purpose binding required")
    scope=matches[0]
    expected={"agentId":str(actor["id"]),"authorityId":str(actor["authority"]),
        "hostId":str(row["host"]),"workspaceId":str(row["workspace"]),
        "contextId":str(row["context_id"]),"nativeThread":row["native_thread"],
        "roleRevision":row["role_revision"],"purpose":"writer",
        "deliveryId":source["deliveryId"],"generation":source["generation"],
        "claimId":source["claimId"],"sourceRequestId":source["requestId"],
        "nativeTurn":source["nativeTurn"],"executorId":source["executorId"],
        "schema":"lax-execution-scope/v2"}
    if not row["enabled"] or any(scope.get(k)!=v for k,v in expected.items()):
        raise Conflict("Accepted purpose context/workspace binding changed")
    from .store import digest
    return {"schema":"lax-purpose-execution-binding/v1","principal":actor["id"],
        "delivery":scope["deliveryId"],"generation":scope["generation"],
        "context_id":scope["contextId"],"native_thread":scope["nativeThread"],
        "workspace_id":scope["workspaceId"],"host":scope["hostId"],
        "role_revision":scope["roleRevision"],"digest":digest(scope)}
