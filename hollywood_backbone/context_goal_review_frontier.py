"""Actionable canonical review cuts for an existing native responsibility.

A frontier is a wake hint, not acceptance, new work, or new authority. The model
must read current evidence and use the original Goal/Work review operations.
"""
from .store import Conflict, Denied, digest
from .goal_contract import current

SETTLED = {"completed", "failed", "cancelled", "unknown"}
REVIEW = {"submitted", "blocked", "failed", "cancelled"}


def native_parent(c, actor_id, goal):
    return bool(c.execute("""SELECT 1 FROM hb_context_material m
        JOIN hb_context_system s ON s.agent=m.agent AND s.enabled
        JOIN hb_delivery d ON d.event=m.source AND d.agent=m.agent
        JOIN hb_event e ON e.id=%s
        WHERE m.agent=%s AND m.kind='work'
          AND m.state IN ('pending','running','waiting')
          AND d.state IN ('claimed','running') AND NOT d.cancellation_requested
          AND (m.source=%s OR (e.source=m.source
            AND e.data->>'type'='goalOrigin'
            AND e.data->>'scope'='explicit-planning-intent/v1'
            AND e.data->>'originRequestId'=m.source::text
            AND e.data->>'goalId'=%s)) LIMIT 1""",
        (goal["source"],actor_id,goal["source"],str(goal["id"]))).fetchone())


def snapshot(c, store, actor, goal):
    result = {"goalId":str(goal["id"]), "revision":goal["revision"], "state":goal["state"]}
    if goal["state"] in ("accepted","cancelled"):
        return result
    policy=c.execute("SELECT reviewer FROM hb_goal_cooperation WHERE goal=%s",
                     (goal["id"],)).fetchone()
    reviewer=policy["reviewer"] if policy and policy["reviewer"] else goal["owner"]
    if reviewer != actor["id"]:
        return result
    contract=current(c,goal)
    valid=not goal["contract_required"] or contract["strategyCurrent"]
    if goal["contract_required"]:
        ids=(contract["strategy"]["strategy"]["frontierWorkIds"] if valid else [])
        rows=c.execute("""SELECT w.id,w.room,w.state,w.revision,w.report_event,
            w.goal_definition_revision,d.state AS delivery_state
            FROM hb_work w LEFT JOIN hb_delivery d ON d.id=w.delivery
            WHERE w.goal=%s AND w.id=ANY(%s::uuid[]) ORDER BY w.id""",
            (goal["id"],ids)).fetchall()
        valid=valid and len(rows)==len(ids) and all(
            w["goal_definition_revision"]==goal["adopted_definition_revision"] for w in rows)
    else:
        rows=c.execute("""SELECT w.id,w.room,w.state,w.revision,w.report_event,
            w.goal_definition_revision,d.state AS delivery_state
            FROM hb_work w LEFT JOIN hb_delivery d ON d.id=w.delivery
            WHERE w.goal=%s ORDER BY w.id LIMIT 33""",(goal["id"],)).fetchall()
        if len(rows)>32:
            raise Conflict("Select a bounded current strategy before native Goal review")
    records=[];unavailable=False;rooms={}
    for row in rows:
        room=str(row["room"])
        if room not in rooms:
            try:store.room(c,actor,row["room"]);rooms[room]=True
            except Denied:rooms[room]=False
        if not rooms[room]:
            unavailable=True
            records.append({"workId":str(row["id"]),"access":"unavailable"})
        else:
            records.append({"workId":str(row["id"]),"revision":row["revision"],
                "state":row["state"],"deliveryState":row["delivery_state"],
                "reportEventId":str(row["report_event"]) if row["report_event"] else None})
    actionable=(not valid or unavailable or
        any(w["state"] in REVIEW and w["delivery_state"] in SETTLED
            or w["delivery_state"] in ("failed","unknown")
            or w["state"]=="assigned" and w["delivery_state"]=="completed" for w in rows) or
        bool(rows) and all(w["state"]=="accepted" and w["delivery_state"] in SETTLED for w in rows))
    cut={"goalId":str(goal["id"]),"adoptedDefinitionRevision":goal["adopted_definition_revision"],
         "planRevision":goal["plan_revision"],"current":valid,"work":records}
    result["reviewFrontier"]={"version":1,"sha256":digest(cut),
        "actionable":bool(actionable),"workCount":len(records)}
    return result


def changed(previous, current):
    old={x["goalId"]:x.get("reviewFrontier",{}).get("sha256") for x in previous}
    return any(x.get("reviewFrontier",{}).get("actionable") and
        old.get(x["goalId"]) != x["reviewFrontier"]["sha256"] for x in current)
