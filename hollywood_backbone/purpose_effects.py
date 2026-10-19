"""Business authority for an accepted purpose-native writer turn.

The scope is an immutable context-begin receipt, not a synthetic legacy binding.
Process liveness still comes only from the enrolled independent host controller.
"""
from .store import Conflict, Denied


def validate(c, actor, value, request):
    b=c.execute("""SELECT b.*,s.host,s.role_revision,s.enabled FROM hb_purpose_context b
        JOIN hb_context_system s ON s.agent=b.agent WHERE b.agent=%s AND b.purpose='writer'
        AND b.token_digest=%s""",(actor["id"],request["tokenDigest"])).fetchone()
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
