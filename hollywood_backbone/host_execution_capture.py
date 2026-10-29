"""Read-only execution facts for the canonical host controller, not new work.

A host receives only its own original executor's current delivery and role.
No agent credentials, administrative credential or mutation authority is returned.
"""
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
    return {"schema": "hollywood-host-execution-capture/v1",
            "snapshot": store.claim_receipt(connection, str(delivery["id"])),
            "role": json_value(role), "admitsExecution": False}
