"""Exact canonical Goal prerequisites for durable purpose-native work.

The existing Goal/Work domain owns acceptance. This is only an internal wait
frontier; no model, work assignment, Goal transition or message is created here.
"""
from uuid import UUID
from .store import Conflict, Denied
from .goal_contract import ContractConflict
from .context_system import item, change
from .context_peers import require_current_read, own_change

TERMINAL={"accepted","cancelled"}

def goals(c,store,actor,work,ids):
    from .work import goal_row
    rows=[]
    for identity in ids:
        goal=goal_row(store,c,actor,str(UUID(identity)))
        origin=store.readable_event(c,actor,goal["source"])
        matches=goal["source"]==work["source"]
        if not matches:
            matches=(origin["data"].get("type")=="goalOrigin"
              and origin["data"].get("scope")=="explicit-planning-intent/v1"
              and origin["source"]==work["source"]
              and origin["data"].get("originRequestId")==str(work["source"])
              and origin["data"].get("goalId")==str(goal["id"]))
        if not matches:raise Denied("Goal is not sourced from this original responsibility")
        rows.append(goal)
    return rows

def wait(c,store,actor,binding,p):
    v=p["value"]
    if set(v)!={"workId","goalIds"}:raise ValueError("Exact original work and canonical Goals required")
    ids=v["goalIds"]
    if (not isinstance(ids,list) or not 1<=len(ids)<=8
            or any(not isinstance(x,str) for x in ids) or len(set(ids))!=len(ids)):
        raise ValueError("One to eight distinct canonical Goals required")
    work=item(c,store,actor,v["workId"],"work")
    require_current_read(binding,work,p["nativeTurn"])
    d=c.execute("SELECT state,cancellation_requested FROM hb_delivery WHERE agent=%s AND event=%s",
                 (actor["id"],work["source"])).fetchone()
    if (work["state"] not in ("pending","running") or not d
            or d["state"] not in ("claimed","running") or d["cancellation_requested"]):
        raise Conflict("Original responsibility no longer admits a wait")
    old=work["value"].get("goalPrerequisites",[])
    combined=list(dict.fromkeys(old+[str(UUID(x)) for x in ids]))
    if len(combined)>8:raise ValueError("Responsibility prerequisite limit exceeded")
    rows=goals(c,store,actor,work,combined)
    from .context_goal_review_frontier import snapshot as review_snapshot
    snapshot=[review_snapshot(c,store,actor,g) for g in rows]
    if any(x.get("reviewFrontier",{}).get("actionable") for x in snapshot):
        raise ContractConflict("NATIVE_GOAL_REVIEW_REQUIRED","Canonical Goal review is actionable for this responsibility; inspect current reports/plan/evidence before waiting")
    waiting=any(g["state"] not in TERMINAL for g in rows)
    own_change(c,actor,binding,work,"waiting" if waiting else work["state"],
      {**work["value"],"goalPrerequisites":combined,"goalResults":snapshot},p["nativeTurn"])
    return {"workId":str(work["id"]),"state":"waiting" if waiting else "results_available",
       "goals":snapshot,"instruction":"End this inference while waiting; new actionable current review or terminal Goal facts resume this same responsibility. Review evidence explicitly before final response." if waiting else "Read current work and canonical results; terminal cancellation is not successful acceptance."}

def source_goals(c,actor,work):
    rows=c.execute("""SELECT g.id FROM hb_goal g JOIN hb_event e ON e.id=g.source
        WHERE g.authority=%s AND (g.source=%s OR (e.source=%s
          AND e.data->>'type'='goalOrigin'
          AND e.data->>'scope'='explicit-planning-intent/v1'
          AND e.data->>'originRequestId'=%s AND e.data->>'goalId'=g.id::text))
        ORDER BY g.id""",(actor["authority"],work["source"],work["source"],str(work["source"]))).fetchall()
    return list(dict.fromkeys(work["value"].get("goalPrerequisites",[])+[str(g["id"]) for g in rows]))

def require_resolved(c,store,actor,work):
    ids=source_goals(c,actor,work)
    if ids and any(g["state"] not in TERMINAL for g in goals(c,store,actor,work,ids)):
        raise Conflict("Canonical Goal prerequisites remain unresolved")

def refresh(c,store,actor):
    rows=c.execute("""SELECT * FROM hb_context_material m WHERE agent=%s AND kind='work'
       AND state IN ('waiting','pending','running')
       AND (value ? 'goalPrerequisites' OR EXISTS(SELECT 1 FROM hb_goal g JOIN hb_event e ON e.id=g.source
           WHERE g.authority=%s AND (g.source=m.source OR (e.source=m.source
             AND e.data->>'type'='goalOrigin' AND e.data->>'scope'='explicit-planning-intent/v1'
             AND e.data->>'originRequestId'=m.source::text AND e.data->>'goalId'=g.id::text))))
       AND NOT EXISTS(SELECT 1 FROM hb_delivery d WHERE d.event=m.source AND d.agent=m.agent
           AND d.cancellation_requested) ORDER BY created_at LIMIT 100 FOR UPDATE""",(actor["id"],actor["authority"])).fetchall()
    for work in rows:
        ids=source_goals(c,actor,work)
        if not ids:continue
        try:current=goals(c,store,actor,work,ids)
        except Denied:continue
        from .context_goal_review_frontier import snapshot as review_snapshot, changed
        snapshot=[review_snapshot(c,store,actor,g) for g in current]
        previous=work["value"].get("goalResults",[])
        terminal=all(g["state"] in TERMINAL for g in current)
        if not terminal and not changed(previous,snapshot):continue
        if snapshot==previous:continue
        change(c,work,"pending" if work["state"]=="waiting" else work["state"],
            {**work["value"],"goalResults":snapshot,
             "resumeRevision":work["value"].get("resumeRevision",0)+1})
