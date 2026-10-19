"""Reconcile OS-stop/native-terminal ordering without clearing domain effects."""
from .store import json_value

def reconcile(store, c, delivery):
    d = c.execute("SELECT * FROM hb_delivery WHERE id=%s FOR UPDATE", (delivery,)).fetchone()
    if not d or d["state"] != "unknown" or d["cancellation_requested"]:
        return d
    # Native purpose Work never uses legacy execution transitions. An explicit
    # unknown transition is independent evidence and cannot be cleared here.
    if c.execute("SELECT 1 FROM hb_transition WHERE delivery=%s LIMIT 1", (delivery,)).fetchone():
        return d
    system = c.execute("""SELECT s.* FROM hb_context_system s
        JOIN hb_principal a ON a.id=s.agent AND a.active
        JOIN hb_placement l ON l.agent=s.agent AND l.host=s.host AND l.enabled
        JOIN hb_purpose_context b ON b.agent=s.agent AND b.purpose='writer'
        WHERE s.agent=%s AND s.host=%s AND s.enabled AND b.active_turn IS NULL""",
        (d["agent"], d["host"])).fetchone()
    if not system:
        return d
    before_first_inference = d["native_turn"] is None
    if before_first_inference:
        # Resident capture precedes writer reservation. Preserve an original
        # admitted claim after a failed resident setup only when no inference
        # was ever reserved and no native effect/activity exists for this Work.
        if c.execute("""SELECT 1 FROM hb_context_permit p
            JOIN hb_context_material m ON m.id=ANY(p.material_ids)
            WHERE p.agent=%s AND p.purpose='writer' AND m.agent=p.agent
              AND m.kind='work' AND m.source=%s LIMIT 1""", (d["agent"],d["event"])).fetchone():
            return d
        for table in ("hb_native_effect", "hb_activity"):
            if c.execute("SELECT 1 FROM "+table+" WHERE delivery=%s LIMIT 1",(delivery,)).fetchone():
                return d
    elif not c.execute("""SELECT 1 FROM hb_context_permit
        WHERE agent=%s AND purpose='writer' AND native_thread=%s AND native_turn=%s
          AND state='released' AND terminal_status IN ('completed','interrupted','failed')""",
        (d["agent"],d["native_thread"],d["native_turn"])).fetchone():
        return d
    stopped = c.execute("""SELECT x.id FROM hb_executor x
        JOIN hb_host_admission h ON h.host=x.host AND h.controller=x.controller
        JOIN hb_event e ON e.source=%s AND e.author=x.controller AND e.authority=x.authority
          AND e.data->>'type'='executorState' AND e.data->>'resourceState'='stopped'
          AND e.data->>'deliveryState'='unknown' AND e.data->>'executorId'=x.id::text
          AND (e.data->>'generation')::bigint=%s AND e.created_at >= %s
        WHERE x.delivery=%s AND x.generation=%s AND x.claim_id=%s AND x.state='stopped'
        ORDER BY e.seq DESC LIMIT 1""",
        (d["event"],d["generation"],d["updated_at"],delivery,d["generation"],d["claim_id"])).fetchone()
    if not stopped:
        return d
    if c.execute("""SELECT 1 FROM hb_edit_permit WHERE state<>'settled'
        AND (delivery=%s OR source->>'deliveryId'=%s) LIMIT 1""", (delivery,str(delivery))).fetchone():
        return d
    for table in ("hb_attention", "hb_native_control"):
        states = "('closed','expired')" if table=="hb_attention" else "('native_recorded','rejected')"
        if c.execute(f"SELECT 1 FROM {table} WHERE delivery=%s AND generation=%s AND state NOT IN {states} LIMIT 1",
                     (delivery,d["generation"])).fetchone():
            return d
    actor=store.actor(c, d["agent"])
    event=store.readable_event(c,actor,d["event"])
    store.room(c,actor,event["room"],"can_post")
    if event["data"].get("goalId") and not c.execute("SELECT 1 FROM hb_goal WHERE id=%s AND state='active'",
                                                    (event["data"]["goalId"],)).fetchone():
        return d
    after=c.execute("""UPDATE hb_delivery SET state=%s,revision=revision+1,
        updated_at=clock_timestamp() WHERE id=%s RETURNING *""",
        ("claimed" if before_first_inference else "running",delivery)).fetchone()
    store.event(c,actor,event["room"],"progress",
        ("Original resident setup stopped before any writer inference reservation. The same admitted claim is preserved; no execution was replayed."
         if before_first_inference else
         "Original native terminal receipt reconciled after executor stop. Unfinished Work and all independent tool-effect evidence remain preserved; no execution was replayed."),
        audience=event["audience"],recipient=event["author"] if event["audience"]=="direct" else None,
        source=event["id"],data={"type":"purposeInferenceReconciled","deliveryId":str(delivery),
        "generation":d["generation"],"nativeTurn":d["native_turn"],"executorId":str(stopped["id"]),
        "reconciliationKind":"before-first-inference" if before_first_inference else "native-terminal",
        "effectOutcomeInferred":False,"newExecutionAdmitted":False})
    return after
