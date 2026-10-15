"""Work identity survives rework; original execution and evidence stay traceable."""

from .store import json_value
from .work import work_row


def record(c, work, delivery, request):
    number = c.execute(
        "SELECT COALESCE(max(number),0)+1 AS n FROM hb_work_attempt WHERE work=%s",
        (work["id"],),
    ).fetchone()["n"]
    c.execute(
        """INSERT INTO hb_work_attempt
           (delivery,authority,work,number,owner,request_event,specification,acceptance,origin)
           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,'native-admission')""",
        (delivery, work["authority"], work["id"], number, work["owner"], request,
         work["specification"], work["acceptance"]),
    )
    return number


def read(store, actor_id, identity, after=0, limit=100):
    with store.pool.connection() as c:
        actor = store.actor(c, actor_id)
        work_row(store, c, actor, identity)
        rows = c.execute(
            """SELECT a.*,d.state AS execution_state,d.generation,d.host,
                      d.native_thread,d.native_turn,d.result_event,
                      b.native_goal_id,b.context_id,b.workspace_id,
                      r.event AS recovery_event,r.principal AS recovery_principal,
                      r.evidence AS recovery_evidence
               FROM hb_work_attempt a JOIN hb_delivery d ON d.id=a.delivery
               LEFT JOIN hb_native_goal_binding b
                 ON b.delivery=d.id AND b.generation=d.generation
               LEFT JOIN hb_work_recovery r ON r.delivery=a.delivery
               WHERE a.work=%s AND a.number>%s ORDER BY a.number LIMIT %s""",
            (identity, after, limit + 1),
        ).fetchall()
        for row in rows[:limit]:
            store.readable_event(c, actor, row["request_event"])
        return json_value({"workId": identity, "attempts": rows[:limit],
                           "nextCursor": rows[limit - 1]["number"] if len(rows) > limit else None})
