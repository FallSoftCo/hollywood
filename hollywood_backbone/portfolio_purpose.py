"""Reuse an original purpose responsibility for its terminal canonical Goal.

This records dispatch coverage, never semantic review or work acceptance. The
same original response is still owed; no self-request or second context is made.
"""
def cover_terminal_goal(c,actor,item,goal,snapshot_digest):
    origin=c.execute("SELECT * FROM hb_event WHERE id=%s",(goal["source"],)).fetchone()
    original=origin["id"]
    if origin["data"].get("type")=="goalOrigin":
        if (origin["data"].get("scope")!="explicit-planning-intent/v1"
                or origin["data"].get("goalId")!=str(goal["id"])
                or origin["data"].get("originRequestId")!=str(origin["source"])):
            return False
        original=origin["source"]
    found=c.execute("""SELECT d.event FROM hb_context_material m
        JOIN hb_context_system s ON s.agent=m.agent AND s.enabled
        JOIN hb_delivery d ON d.agent=m.agent AND d.event=m.source
        WHERE m.agent=%s AND m.kind='work' AND m.source=%s
          AND m.state NOT IN ('cancelled','superseded')
          AND d.state IN ('pending','claimed','running') AND NOT d.cancellation_requested
        ORDER BY m.created_at LIMIT 1""",(actor["id"],original)).fetchone()
    if not found:return False
    c.execute("""UPDATE hb_portfolio_item SET dispatched_seq=latest_seq,
        dispatched_digest=%s,request_event=%s
        WHERE principal=%s AND scope='goal' AND reference=%s""",
        (snapshot_digest,found["event"],actor["id"],item["reference"]))
    return True
