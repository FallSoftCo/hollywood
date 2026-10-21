"""Canonical assignments remain distinct from conversation amendments."""
from .store import Conflict

class AssignmentConflict(Conflict):
    code="CANONICAL_ASSIGNMENT_REQUIRES_WORK"
    def __init__(self):
        super().__init__("This is an assigned canonical Work item. Accept it as its own execution responsibility; do not merge it into another Work or answer it as completed.")

def current(c,actor,source):
    row=c.execute("""SELECT w.id,w.goal,a.delivery FROM hb_work_attempt a
        JOIN hb_work w ON w.id=a.work
        JOIN hb_delivery d ON d.id=a.delivery
        JOIN hb_event e ON e.id=a.request_event
        WHERE a.authority=%s AND a.owner=%s AND a.request_event=%s
          AND d.agent=a.owner AND d.event=a.request_event
          AND e.kind='request' AND e.recipient=a.owner
          AND e.data->>'workId'=w.id::text
          AND e.data->>'goalId'=w.goal::text
          AND (e.data->>'attemptId' IS NULL OR e.data->>'attemptId'=d.id::text)""",
        (actor["authority"],actor["id"],source["source"])).fetchone()
    return ({"workId":str(row["id"]),"goalId":str(row["goal"]),
        "deliveryId":str(row["delivery"]),"requiredInterpretation":"accept_work",
        "meaning":"A distinct assigned responsibility, including finite assignments to this same agent. It is not an amendment to another execution. Check current input/cancellation state; this descriptor never revives closed work."} if row else None)

def require_separate(c,actor,source):
    if current(c,actor,source):
        raise AssignmentConflict()
