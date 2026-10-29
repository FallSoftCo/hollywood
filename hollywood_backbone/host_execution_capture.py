"""Read-only execution facts for the canonical host controller, not new work.

A host receives only its own original executor's current delivery and role.
No agent credentials, administrative credential or mutation authority is returned.
"""
from uuid import UUID

from .store import Conflict, Denied, json_value


def snapshot(store, connection, actor, executor):
    if not actor["administrator"]:
        if actor["kind"] != "service":
            raise Denied("Host capture requires the enrolled controller")
        policy = connection.execute(
            "SELECT controller FROM hb_host_admission WHERE host=%s AND authority=%s FOR SHARE",
            (executor["host"], actor["authority"]),
        ).fetchone()
        if not policy or policy["controller"] != actor["id"]:
            raise Denied("Host capture belongs to another controller")
    delivery = connection.execute(
        "SELECT * FROM hb_delivery WHERE id=%s AND authority=%s FOR SHARE",
        (executor["delivery"], actor["authority"]),
    ).fetchone()
    if not delivery or any(delivery[key] != executor[key] for key in (
            "agent", "host", "generation", "claim_id")):
        raise Conflict("Original executor no longer owns the current delivery generation")
    role = connection.execute(
        "SELECT * FROM hb_agent_role WHERE principal=%s AND authority=%s FOR SHARE",
        (executor["agent"], actor["authority"]),
    ).fetchone()
    if not role:
        raise Denied("Executor role unavailable")
    receipt = store.claim_receipt(connection, str(delivery["id"]))
    work = goal = None
    work_id = receipt["request"].get("data", {}).get("workId")
    if work_id is not None:
        try:
            work_id = str(UUID(work_id))
        except (ValueError, TypeError, AttributeError):
            raise Conflict("Original execution work identity is invalid") from None
        work = connection.execute(
            "SELECT * FROM hb_work WHERE id=%s AND authority=%s AND owner=%s "
            "AND delivery=%s AND request_event=%s FOR SHARE",
            (work_id, actor["authority"], executor["agent"], delivery["id"], delivery["event"]),
        ).fetchone()
        if not work:
            raise Conflict("Original execution no longer owns this work assignment")
        goal = connection.execute(
            "SELECT * FROM hb_goal WHERE id=%s AND authority=%s FOR SHARE",
            (work["goal"], actor["authority"]),
        ).fetchone()
        if not goal:
            raise Conflict("Original execution goal unavailable")
    return {"schema": "hollywood-host-execution-capture/v1",
            "snapshot": receipt, "role": json_value(role),
            "work": json_value(work), "goal": json_value(goal), "admitsExecution": False}
