"""Logical claims for installed purpose agents; inference owns scarce capacity."""
from .store import Denied

def enabled(c,actor,host):
    return c.execute("""SELECT 1 FROM hb_context_system s JOIN hb_purpose_context b
        ON b.agent=s.agent AND b.purpose='writer'
        WHERE s.agent=%s AND s.host=%s AND s.enabled AND b.work_scheduling=2""",
        (actor["id"],host)).fetchone() is not None

def diagnose(store,c,actor,host,identity):
    row=c.execute("""SELECT d.id,e.created_at,e.data,m.state AS material_state FROM hb_delivery d
        JOIN hb_event e ON e.id=d.event JOIN hb_context_material m
          ON m.source=d.event AND m.agent=d.agent AND m.kind='work'
        WHERE d.id=%s AND d.agent=%s AND d.authority=%s AND d.state='pending'
          AND NOT d.cancellation_requested AND m.state IN ('pending','running')""",
        (identity,actor["id"],actor["authority"])).fetchone()
    result={"hostId":str(host),"eligibleDeliveryId":None,"pending":None,
        "busy":None,"enabled":True,"reason":"no_pending_work","capacity":None,
        "resourceReserved":False,"claimKind":"durable_work"}
    if not row:return result
    event=store.readable_event(c,actor,c.execute("SELECT event FROM hb_delivery WHERE id=%s",
                                              (identity,)).fetchone()["event"])
    store.room(c,actor,event["room"],"can_post")
    result["pending"]={"id":row["id"],"created_at":row["created_at"]}
    unresolved=c.execute("""SELECT id,state FROM hb_delivery WHERE agent=%s
        AND state IN ('unknown','cancel_requested') ORDER BY updated_at LIMIT 1""",
        (actor["id"],)).fetchone()
    if unresolved:
        result.update(reason="unresolved_execution",busy=unresolved);return result
    if not c.execute("SELECT 1 FROM hb_host_admission WHERE host=%s",(host,)).fetchone():
        result["reason"]="host_policy_missing";return result
    control=c.execute("SELECT 1 FROM hb_host_control_agent WHERE host=%s AND agent=%s",
                      (host,actor["id"])).fetchone()
    result.update(eligibleDeliveryId=row["id"],reason="eligible",
      queueClass="interactive" if control and row["data"].get("authorKind")=="human" else "worker")
    # Goal lifecycle, canonical cooperation, role, room and exact delivery locks
    # are rechecked by the SAME claim_candidate/claim transaction as legacy work.
    # Native start separately needs the existing per-inference reservation.
    return result
