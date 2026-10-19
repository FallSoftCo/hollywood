"""Causal peer follow-ups amend the delegator's original work, not a new task."""
from psycopg.types.json import Jsonb
from .store import Conflict, Denied
from .context_system import change, item, need_for
from .context_peers import require_current_read

def relation(c, agent, event):
    e=c.execute("SELECT * FROM hb_event WHERE id=%s",(event,)).fetchone()
    if not e or e["kind"]!="request" or e["recipient"]!=agent or not e["source"]:
        return None
    # Match an actual outbound delegation and its actual addressed peer.
    row=c.execute("""SELECT n.id,n.value,w.id AS work_id FROM hb_context_material n
        JOIN hb_context_material w ON w.id=(n.value->>'parentWorkId')::uuid AND w.agent=n.agent
        WHERE n.agent=%s AND n.kind='need' AND n.value->>'kind'='request'
        AND n.value->>'requestEventId'=%s AND n.value->'target'->>'recipientId'=%s
        AND w.kind='work' AND w.state IN ('pending','running','waiting')
        ORDER BY n.created_at LIMIT 2""",(agent,str(e["source"]),str(e["author"]))).fetchall()
    if len(row)!=1:
        return None
    return {"peerContinuation":True,"parentWorkId":str(row[0]["work_id"]),
        "delegationId":str(row[0]["id"]),"delegationEventId":str(e["source"]),
        "responseRequired":True}

def refresh(c, store, actor):
    """One bounded upgrade reconciliation; retain original input/work/receipts."""
    rows=c.execute("""SELECT m.* FROM hb_context_material m JOIN hb_delivery d
        ON d.event=m.source AND d.agent=m.agent WHERE m.agent=%s AND m.kind='input'
        AND m.state IN ('pending','handled') AND d.state='pending' AND d.generation=0
        AND NOT (m.value ? 'peerContinuation') ORDER BY m.created_at LIMIT 100""",
        (actor["id"],)).fetchall()
    for source in rows:
        relationship=relation(c,actor["id"],source["source"])
        if not relationship:
            continue
        store.readable_event(c,actor,source["source"])
        child=c.execute("""SELECT * FROM hb_context_material WHERE agent=%s
            AND kind='work' AND source=%s""",(actor["id"],source["source"])).fetchone()
        if source["state"]=="handled" and (not child or child["state"]!="pending"
                or child["value"].get("execution") or child["value"].get("peerRequests")):
            continue
        change(c,source,"pending",{**source["value"],**relationship,
            "priorInterpretationState":source["state"],
            **({"unadmittedWorkId":str(child["id"])} if child else {})})

def response_material(c,store,actor,binding,p):
    v=p["value"]
    if set(v)!={"workId","inputId","body"}:
        raise ValueError("Exact original work, interpreted input and response material required")
    work=item(c,store,actor,v["workId"],"work")
    require_current_read(binding,work,p["nativeTurn"])
    if work["state"] not in ("running","pending"):
        raise Conflict("Current executing original work required")
    source=item(c,store,actor,v["inputId"],"input")
    if source["state"]!="handled" or source["value"].get("appliedToWorkId")!=str(work["id"]):
        raise Denied("Reader-interpreted input on this original work required")
    if not any(u["inputId"]==str(source["id"]) for u in work["value"].get("updates",[])):
        raise Denied("Original work has no such interpreted contribution")
    need=need_for(c,store,actor,source["source"])
    if need["state"]!="waiting" or need["value"].get("workId")!=str(work["id"]):
        raise Conflict("Original required response is no longer waiting")
    body=v["body"]
    if not isinstance(body,str) or not body.strip() or len(body)>4000:
        raise ValueError("Bounded verified response material required")
    change(c,need,"ready",{**need["value"],"material":body,"prerequisites":[],
        "workStillOpen":True,"materialWorkRevision":work["revision"]})
    return {"communicationId":str(need["id"]),"state":"ready","workCompleted":False,
        "meaning":"Verified writer material for the responder; original work remains open."}
