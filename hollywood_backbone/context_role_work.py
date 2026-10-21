"""Bounded native work originating in an ongoing role, not a synthetic request.

The existing role is authority; an ordinary external observation is only source
evidence. Canonical events/deliveries own admission. Context material remains
the same internal Work/result handoff used by addressed requests.
"""
from uuid import UUID, uuid5
from psycopg.types.json import Jsonb

from .store import Conflict, Denied, json_value, digest
from .context_system import item, change
from .context_peers import require_current_read


def validate(c, store, actor, event):
    """Fence original role work after responsibility or room access changes."""
    if event["kind"] != "control" or event["data"].get("type") != "roleWork":
        return
    role = c.execute("SELECT * FROM hb_agent_role WHERE principal=%s FOR SHARE",
                     (actor["id"],)).fetchone()
    if (event["author"] != actor["id"] or event["recipient"] != actor["id"]
            or not role or role["native_goal_scope"] != "role"
            or role["revision"] != event["data"].get("roleRevision")):
        raise Conflict("Original standing responsibility changed; reconcile this role work")
    original = store.readable_event(c, actor, event["source"])
    if original["audience"] != "room" or original["room"] != event["room"]:
        raise Denied("Original role-work source audience changed")
    store.room(c, actor, event["room"], "can_request")


def origin(c, actor, event):
    """Recognize actual native role admission, never caller-provided event flags."""
    if event["kind"] != "control" or event["data"].get("type") != "roleWork":
        return False
    return bool(c.execute("""SELECT 1 FROM hb_delivery d JOIN hb_context_material m
        ON m.agent=d.agent AND m.source=d.event AND m.kind='work'
        WHERE d.event=%s AND d.agent=%s
          AND m.value->'roleInitiative'->>'observationId'=%s
          AND m.value->'roleInitiative'->>'roleRevision'=%s""",
        (event["id"], event["recipient"], event["data"].get("observationId"),
         str(event["data"].get("roleRevision")))).fetchone())


def initiate(c, store, actor, binding, p):
    v = p["value"]
    if set(v) != {"observationId", "roleRevision", "objective", "acceptance", "body"}:
        raise ValueError("Exact observation, role revision, objective, acceptance and interpretation required")
    for key in ("objective", "acceptance", "body"):
        if not isinstance(v[key], str) or not v[key].strip() or len(v[key]) > 4000:
            raise ValueError("Bounded nonempty role-work contributions required")
    if type(v["roleRevision"]) is not int or v["roleRevision"] < 1:
        raise ValueError("Exact standing role revision required")
    source = item(c, store, actor, str(UUID(v["observationId"])), "input")
    require_current_read(binding, source, p["nativeTurn"])
    if source["state"] != "pending" or source["value"].get("roomObservation") is not True:
        raise Conflict("Pending ordinary room observation required")
    role = c.execute("SELECT * FROM hb_agent_role WHERE principal=%s FOR SHARE",
                     (actor["id"],)).fetchone()
    if not role or role["native_goal_scope"] != "role":
        raise Denied("Administrator-enrolled ongoing role required; an observation grants no authority")
    if role["revision"] != v["roleRevision"] or binding["reads"].get("role") != {
            "revision": role["revision"], "turn": p["nativeTurn"]}:
        raise Conflict("Read the exact current standing role before initiating work")
    incoming = store.readable_event(c, actor, source["source"])
    if (incoming["author"] == actor["id"] or incoming["kind"] not in ("message", "request")
            or incoming["data"].get("authorKind") not in ("human", "agent")
            or incoming["data"].get("type") == "peerCompletion"):
        raise Denied("A distinct attributable external room fact is required")
    if incoming["audience"] != "room":
        raise Denied("Private observations require an explicit sharing contract for new role work")
    store.room(c, actor, incoming["room"], "can_request")
    store.room(c, actor, incoming["room"], "can_post")
    cancelled = c.execute("""SELECT 1 FROM hb_delivery WHERE event=%s
        AND cancellation_requested LIMIT 1""", (incoming["id"],)).fetchone()
    if cancelled:
        raise Conflict("Cancelled source cannot authorize new role work")
    authority = {"schema": "lax-role-work/v1", "observationId": str(source["id"]),
                 "sourceEventId": str(incoming["id"]), "roleRevision": role["revision"],
                 "roleSha256": digest(json_value(role)),
                 "readerContextId": str(binding["context_id"]),
                 "nativeThread": p["nativeThread"], "nativeTurn": p["nativeTurn"]}
    event = store.event(c, actor, incoming["room"], "control", v["objective"],
        recipient=actor["id"], source=incoming["id"],
        data={"type": "roleWork", **authority, "acceptance": v["acceptance"]})
    delivery_id = str(uuid5(UUID(str(event)), "role-delivery"))
    work_id = str(uuid5(UUID(str(event)), "work"))
    need_id = str(uuid5(UUID(str(event)), "response"))
    c.execute("INSERT INTO hb_delivery(id,authority,event,agent) VALUES(%s,%s,%s,%s)",
              (delivery_id, actor["authority"], event, actor["id"]))
    c.execute("""INSERT INTO hb_context_material(id,agent,kind,source,state,value)
        VALUES(%s,%s,'work',%s,'pending',%s)""",
        (work_id, actor["id"], event, Jsonb({
            "objective": v["objective"], "acceptance": v["acceptance"],
            "progress": [], "evidence": [], "roleInitiative": {
                **authority, "roleSnapshot": json_value(role), "interpretation": v["body"]}})))
    c.execute("""INSERT INTO hb_context_material(id,agent,kind,source,state,value)
        VALUES(%s,%s,'need',%s,'waiting',%s)""",
        (need_id, actor["id"], event, Jsonb({"required": True, "workId": work_id,
            "responseKind": "work_result", "prerequisites": ["result:" + work_id]})))
    change(c, source, "handled", {**source["value"], "interpretation": v["body"],
                                "initiatedWorkId": work_id})
    return {"sourceEventId": str(event), "externalSourceEventId": str(incoming["id"]),
            "deliveryId": delivery_id, "workId": work_id, "communicationId": need_id,
            "roleRevision": role["revision"], "workState": "pending",
            "meaning": "Native bounded role work accepted; original tasks unchanged. No human request or chat self-request created."}


def decision_owner(c, event):
    """Resolve captured role authority, never the human behind source evidence."""
    from .store import Store
    material = c.execute("""SELECT value FROM hb_context_material
        WHERE source=%s AND agent=%s AND kind='work'""",
        (event["id"], event["recipient"])).fetchone()
    authority = material["value"].get("roleInitiative", {}) if material else {}
    role = authority.get("roleSnapshot", {})
    if (not role or digest(role) != event["data"].get("roleSha256")
            or role.get("revision") != event["data"].get("roleRevision")
            or role.get("native_goal_scope") != "role"
            or role.get("principal") != str(event["recipient"])
            or not role.get("decision_owner")):
        raise Conflict("Role work needs an explicitly captured human decision owner; evidence is not approval authority")
    human = c.execute("""SELECT * FROM hb_principal WHERE id=%s AND authority=%s
        AND kind='human' AND active""", (role["decision_owner"], event["authority"])).fetchone()
    if not human:
        raise Denied("Captured role decision owner is unavailable; no fallback is authorized")
    Store.room(c, human, event["room"])
    Store.room(c, human, event["room"], "can_request")
    return human["id"]
