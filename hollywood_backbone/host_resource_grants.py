"""A host may carry existing agent access into its currently executing Work.

This does not enroll roots/agents, restore revoked grants, create edit plans,
admit execution or authorize a filesystem mutation. Those boundaries remain
independent and retain their current canonical checks.
"""
from .store import Conflict, Denied


def authorize(connection, actor, workspace, work_id, payload):
    if actor["kind"] != "service" or payload.get("state") != "active":
        raise Denied("Only an enrolled host may carry current Work access")
    root = connection.execute(
        "SELECT * FROM hb_physical_workspace WHERE id=%s AND authority=%s FOR UPDATE",
        (workspace, actor["authority"]),
    ).fetchone()
    if not root or root["state"] != "active":
        raise Denied("Active local physical workspace required")
    from .executors import controller
    from .collaboration import bridge
    # Hold the exact enrollment while authorizing this metadata mutation;
    # controller replacement/bridge revocation cannot race acceptance.
    connection.execute(
        "SELECT host FROM hb_host_admission WHERE host=%s AND authority=%s FOR SHARE",
        (root["host"], actor["authority"]),
    ).fetchone()
    connection.execute(
        "SELECT host FROM hb_collaboration_bridge WHERE host=%s AND authority=%s FOR SHARE",
        (root["host"], actor["authority"]),
    ).fetchone()
    policy = controller(None, connection, actor, root["host"])
    bridge(connection, actor, str(root["host"]))
    work = connection.execute(
        "SELECT * FROM hb_work WHERE id=%s AND authority=%s FOR UPDATE",
        (work_id, actor["authority"]),
    ).fetchone()
    if not work or work["state"] != "assigned":
        raise Denied("A current assigned Work is required")
    if root["revision"] != payload["workspaceRevision"] or work["revision"] != payload["workRevision"]:
        raise Conflict("Workspace or Work revision changed")
    if not connection.execute(
        "SELECT 1 FROM hb_goal WHERE id=%s AND authority=%s AND state='active'",
        (work["goal"], actor["authority"]),
    ).fetchone():
        raise Denied("Current Work goal does not admit access")
    if not connection.execute(
        "SELECT 1 FROM hb_placement WHERE agent=%s AND host=%s AND enabled FOR SHARE",
        (work["owner"], root["host"]),
    ).fetchone():
        raise Denied("Work owner is not enabled on this host")
    from .agent_context import owner
    connection.execute(
        "SELECT principal FROM hb_agent_context WHERE principal=%s FOR SHARE",
        (work["owner"],),
    ).fetchone()
    context = owner(connection, work["owner"])
    if (not context or context["bindingKind"] != "legacy"
            or context["host"] != root["host"]):
        raise Denied("Persistent Work owner belongs to another execution host")
    access = connection.execute(
        "SELECT * FROM hb_physical_agent_grant WHERE workspace=%s AND principal=%s FOR SHARE",
        (workspace, work["owner"]),
    ).fetchone()
    if (not access or access["state"] != "active" or not access["can_read"]
            or not access["can_write"] or access["execution_host"] != root["host"]):
        raise Denied("Current agent access does not authorize this Work resource")
    delivery = connection.execute(
        "SELECT * FROM hb_delivery WHERE id=%s AND authority=%s FOR SHARE",
        (work["delivery"], actor["authority"]),
    ).fetchone()
    if (not delivery or delivery["agent"] != work["owner"]
            or delivery["host"] != root["host"] or delivery["state"] != "running"
            or delivery["cancellation_requested"] or delivery["event"] != work["request_event"]
            or context["native_thread"] != delivery["native_thread"]):
        raise Denied("Original running Work delivery required")
    event = connection.execute(
        "SELECT kind,recipient,data FROM hb_event WHERE id=%s AND authority=%s",
        (delivery["event"], actor["authority"]),
    ).fetchone()
    data = event["data"] if event else {}
    if (not event or event["kind"] != "request" or event["recipient"] != work["owner"]
            or data.get("workId") != str(work["id"])
            or data.get("goalId") != str(work["goal"])
            or data.get("attemptId") != str(delivery["id"])
            or type(data.get("workRevision")) is not int
            or data["workRevision"] != work["revision"]
            or type(data.get("attemptNumber")) is not int or data["attemptNumber"] < 1
            or data.get("workCompletionPolicy") != "report-required/v1"):
        raise Denied("Original canonical Work assignment metadata required")
    if not connection.execute(
        "SELECT 1 FROM hb_executor WHERE authority=%s AND delivery=%s AND agent=%s "
        "AND host=%s AND controller=%s AND generation=%s AND claim_id=%s "
        "AND state='live' AND launch_phase='attached' "
        "AND observed_at>clock_timestamp()-make_interval(secs=>%s) FOR SHARE",
        (actor["authority"], delivery["id"], work["owner"], root["host"], actor["id"],
         delivery["generation"], delivery["claim_id"], policy["sample_max_age"]),
    ).fetchone():
        raise Denied("Independent current native executor observation required")
    old = connection.execute(
        "SELECT * FROM hb_physical_grant WHERE workspace=%s AND work=%s FOR UPDATE",
        (workspace, work_id),
    ).fetchone()
    if old and old["state"] != "active":
        raise Denied("Host capture cannot restore an explicitly revoked Work grant")
