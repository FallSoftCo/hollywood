"""Protected original administrative acceptance; never store enrollment secrets."""
from psycopg.types.json import Jsonb
from .store import Conflict, Denied, json_value

ACTIONS = {"principal", "create_room", "membership", "placement", "role", "communication", "collaboration_bridge", "physical_workspace", "physical_grant", "physical_agent_grant", "resource_default"}

def mutate(store, actor_id, action, arguments, command=None):
    if action not in ACTIONS:
        raise ValueError("Unknown administrative primitive")
    with store.pool.connection() as c:
        actor = store.actor(c, actor_id)
        store.admin(actor)
        if command:
            # Serialize the same actor/operation before checking/inserting its receipt.
            # Hash collisions only serialize unrelated commands, never equate them.
            c.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                      (str(actor["id"]) + ":" + command["operationId"],))
            old = c.execute(
                "SELECT * FROM hb_administrative_command WHERE principal=%s AND operation=%s",
                (actor["id"], command["operationId"]),
            ).fetchone()
            if old:
                if (old["method"], old["path"], old["wire_digest"]) != (
                    command["method"], command["path"], command["wireDigest"]
                ):
                    raise Conflict("Administrative operation contents changed")
                return old["accepted_result"]
        result = json_value(getattr(store, "_admin_" + action)(c, actor, *arguments))
        if command:
            c.execute(
                """INSERT INTO hb_administrative_command
                   (authority,principal,operation,method,path,wire_digest,accepted_result)
                   VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                (actor["authority"], actor["id"], command["operationId"], command["method"],
                 command["path"], command["wireDigest"], Jsonb(result)),
            )
        return result

def read(store, actor_id, operation):
    with store.pool.connection() as c:
        actor = store.actor(c, actor_id)
        store.admin(actor)
        row = c.execute(
            "SELECT * FROM hb_administrative_command WHERE authority=%s AND principal=%s AND operation=%s",
            (actor["authority"], actor["id"], operation),
        ).fetchone()
        if not row:
            raise Denied("Original administrative command unavailable")
        return json_value({
            "schema": "hollywood-administrative-command/v1",
            "authorityId": row["authority"], "actorId": row["principal"],
            "operationId": row["operation"], "method": row["method"], "path": row["path"],
            "wireDigest": row["wire_digest"], "acceptedResult": row["accepted_result"],
            "acceptedAt": row["accepted_at"],
        })
