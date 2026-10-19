"""Acknowledge only inactive, known terminal or never-admitted work cancellation."""
from .store import json_value

def settle_idle(c,store,actor):
    rows=c.execute("""SELECT m.* FROM hb_context_material m JOIN hb_delivery d
        ON d.event=m.source AND d.agent=m.agent WHERE m.agent=%s AND m.kind='work'
        AND m.state NOT IN ('complete','cancelled','superseded') AND d.cancellation_requested
        AND d.state<>'unknown'
        AND NOT EXISTS(SELECT 1 FROM hb_purpose_context b WHERE b.agent=m.agent
          AND b.active_turn IS NOT NULL AND m.id=ANY(b.active_material))
        AND NOT EXISTS(SELECT 1 FROM hb_context_permit p WHERE p.agent=m.agent
          AND m.id=ANY(p.material_ids) AND p.state IN ('reserved','running'))
        AND (NOT EXISTS(SELECT 1 FROM hb_context_permit p WHERE p.agent=m.agent
                 AND p.purpose='writer' AND m.id=ANY(p.material_ids) AND p.native_turn IS NOT NULL)
          OR EXISTS(SELECT 1 FROM hb_context_permit p WHERE p.agent=m.agent
             AND p.purpose='writer' AND m.id=ANY(p.material_ids) AND p.state='released'
             AND p.native_turn=m.value->'execution'->>'nativeTurn'
             AND p.terminal_status IN ('completed','interrupted','failed')))
        ORDER BY m.created_at LIMIT 100 FOR UPDATE OF m""",(actor["id"],)).fetchall()
    from .work import lock_delivery_goal,acknowledge_cancellation,settle_goal_cancellation
    from .native_controls import terminal
    from .human_continuations import settle
    for material in rows:
        found=c.execute("SELECT * FROM hb_delivery WHERE event=%s AND agent=%s",
                        (material["source"],actor["id"])).fetchone()
        work=lock_delivery_goal(c,found["id"],actor["authority"])
        delivery=c.execute("SELECT * FROM hb_delivery WHERE id=%s FOR UPDATE",(found["id"],)).fetchone()
        if not delivery["cancellation_requested"] or delivery["state"]=="unknown":continue
        c.execute("""UPDATE hb_context_material SET state='cancelled',revision=revision+1,
           updated_at=clock_timestamp() WHERE agent=%s AND source=%s
           AND state NOT IN ('handled','published','complete','cancelled')""",
           (actor["id"],material["source"]))
        if delivery["state"]!="cancelled":
            c.execute("""UPDATE hb_delivery SET state='cancelled',revision=revision+1,
                updated_at=clock_timestamp() WHERE id=%s""",(delivery["id"],))
        c.execute("""UPDATE hb_context_permit SET state='released',terminal_status='cancelled_before_start'
            WHERE agent=%s AND purpose='writer' AND %s=ANY(material_ids)
              AND state='queued' AND native_turn IS NULL""",(actor["id"],material["id"]))
        c.execute("""UPDATE hb_attention SET state=CASE WHEN state='pending' THEN 'expired'
            WHEN state='native_submitted' THEN 'closed' ELSE 'unknown' END,
            revision=revision+1,updated_at=clock_timestamp() WHERE delivery=%s
            AND state IN ('pending','response_submitted','native_submitted')""",(delivery["id"],))
        terminal(store,c,actor,delivery)
        settle(store,c,actor,delivery,"cancelled")
        acknowledge_cancellation(c,work)
        settle_goal_cancellation(store,c,work)
    return len(rows)
