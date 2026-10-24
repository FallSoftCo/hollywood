"""Actionable canonical planning/review cuts for an existing responsibility.

A frontier is a wake hint, not acceptance, new work, or new authority. The model
must read current evidence and use the original Goal/Work review operations.
"""
from .store import Conflict, Denied, digest
from .goal_contract import current

SETTLED = {"completed", "failed", "cancelled", "unknown"}
REVIEW = {"submitted", "blocked", "failed"}


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
    reviewer=policy["reviewer"] if policy and policy["reviewer"] else None
    can_manage=actor["administrator"] or actor["id"] in (goal["owner"],goal["creator"])
    can_review=actor["administrator"] or (actor["id"]==reviewer if reviewer else can_manage)
    review_responsible=actor["id"]==reviewer if reviewer else can_manage
    if not (can_manage or can_review):
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
    complete=bool(rows) and all(w["state"]=="accepted" and w["delivery_state"] in SETTLED for w in rows)
    review_actionable=(not valid or unavailable or
        any(w["state"] in REVIEW and w["delivery_state"] in SETTLED
            or w["delivery_state"] in ("failed","unknown")
            or w["state"]=="assigned" and w["delivery_state"]=="completed" for w in rows) or complete)
    planning=[];cancellation_wait=False;planning_unavailable=False
    definition_pending=goal["definition_revision"]!=goal["adopted_definition_revision"]
    if can_manage:
        # Include unresolved work outside a replaced frontier. A selected plan
        # cannot hide outstanding cancellations or uncertain obsolete effects.
        unsettled=c.execute("""SELECT w.id,w.room,w.state,w.revision,d.state AS delivery_state
            FROM hb_work w LEFT JOIN hb_delivery d ON d.id=w.delivery
            WHERE w.goal=%s AND (w.state NOT IN ('accepted','cancelled')
              OR d.state IN ('pending','claimed','running','cancel_requested','unknown'))
            ORDER BY w.id""",(goal["id"],)).fetchall()
        for w in unsettled:
            room=str(w["room"])
            if room not in rooms:
                try:store.room(c,actor,w["room"]);rooms[room]=True
                except Denied:rooms[room]=False
            if rooms[room]:
                planning.append({"workId":str(w["id"]),"revision":w["revision"],
                    "state":w["state"],"deliveryState":w["delivery_state"]})
            else:
                planning_unavailable=True
                planning.append({"workId":str(w["id"]),"access":"unavailable"})
        cancellation_wait=bool(unsettled) and not planning_unavailable and all(
            w["state"]=="cancel_requested" and w["delivery_state"]!="unknown" for w in unsettled)
    # Submitted evidence belongs to the independent verifier. The planner wakes
    # for adoption/replanning, failed/uncertain effects or a completed frontier;
    # receiving the hint never grants permission to perform a review.
    planning_actionable=(not valid or unavailable or planning_unavailable or complete or
        any(w.get("deliveryState")=="unknown" for w in planning) or
        any(w["state"] in ("blocked","failed","rejected","cancelled") and w["delivery_state"] in SETTLED
            or w["delivery_state"] in ("failed","unknown")
            or w["state"]=="assigned" and w["delivery_state"]=="completed" for w in rows))
    if definition_pending:
        planning_actionable=not cancellation_wait
    # Administrative override is a capability, not a second review assignment.
    # An explicitly designated verifier owns that wake; the planner can await it.
    actionable=(review_responsible and review_actionable or can_manage and planning_actionable)
    cut={"goalId":str(goal["id"]),"definitionRevision":goal["definition_revision"],
         "adoptedDefinitionRevision":goal["adopted_definition_revision"],
         "planRevision":goal["plan_revision"],"current":valid,"work":records,
         "canManage":bool(can_manage),"canReview":bool(can_review),
         "reviewResponsible":bool(review_responsible),"unsettled":planning}
    result["reviewFrontier"]={"version":1,"sha256":digest(cut),
        "actionable":bool(actionable),"workCount":len(records),
        "canManage":bool(can_manage),"canReview":bool(can_review),
        "definitionPending":definition_pending,"waitingForCancellation":cancellation_wait,
        "reviewResponsible":bool(review_responsible)}
    return result


def changed(previous, current):
    old={x["goalId"]:x.get("reviewFrontier",{}).get("sha256") for x in previous}
    return any(x.get("reviewFrontier",{}).get("actionable") and
        old.get(x["goalId"]) != x["reviewFrontier"]["sha256"] for x in current)
