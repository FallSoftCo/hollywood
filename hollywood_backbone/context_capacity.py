"""Native-turn capacity, independent of durable work and resident processes."""
from uuid import UUID, uuid5
from .store import Conflict, Denied, digest, json_value

SCHEMA = """
ALTER TABLE hb_purpose_context ADD COLUMN IF NOT EXISTS observed_at timestamptz;
CREATE TABLE IF NOT EXISTS hb_context_permit(
 id uuid PRIMARY KEY, agent uuid NOT NULL, purpose text NOT NULL, host uuid NOT NULL REFERENCES hb_host(id),
 context_id uuid NOT NULL, native_thread text NOT NULL, native_turn text,
 operation text NOT NULL, fingerprint text NOT NULL, material_ids uuid[] NOT NULL,
 role_revision bigint NOT NULL, queue_class text NOT NULL CHECK(queue_class IN ('interactive','worker')),
 state text NOT NULL CHECK(state IN ('queued','reserved','running','released')),
 terminal_status text, ticket bigserial NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(agent,purpose) REFERENCES hb_purpose_context(agent,purpose),
 UNIQUE(agent,purpose,operation));
CREATE UNIQUE INDEX IF NOT EXISTS hb_context_permit_active ON hb_context_permit(agent,purpose)
 WHERE state IN ('reserved','running');
"""

def resources(c, host):
    # Existing single-context executions keep their original conservative holds.
    # Purpose agents instead hold permits for inference; their resident processes
    # remain independently observed and consume actual host memory/cgroup budget.
    return c.execute("""WITH resources AS (
        SELECT x.delivery,coalesce(t.queue_class,'worker') AS queue_class FROM hb_executor x
        LEFT JOIN hb_admission_trace t ON t.delivery=x.delivery
        WHERE x.host=%s AND x.state<>'stopped' AND NOT EXISTS(
          SELECT 1 FROM hb_context_system s WHERE s.agent=x.agent AND s.enabled)
        UNION ALL SELECT d.id,coalesce(t.queue_class,'worker') FROM hb_delivery d
        LEFT JOIN hb_admission_trace t ON t.delivery=d.id
        WHERE d.host=%s AND d.state IN ('claimed','running','unknown','cancel_requested')
          AND NOT EXISTS(SELECT 1 FROM hb_executor x WHERE x.delivery=d.id AND x.generation=d.generation)
          AND NOT EXISTS(SELECT 1 FROM hb_context_system s WHERE s.agent=d.agent AND s.enabled)
        UNION ALL SELECT id,queue_class FROM hb_context_permit
        WHERE host=%s AND state IN ('reserved','running'))
        SELECT count(*) AS total,count(*) FILTER(WHERE queue_class='worker') AS workers FROM resources""",
        (host,host,host)).fetchone()


def reserve(store,c,actor,purpose,binding,p):
    from .admission import lock
    if set(p["value"]) != {"materialIds"}:
        raise ValueError("Exact original material set required")
    ids=p["value"]["materialIds"]
    if (not isinstance(ids,list) or not 1<=len(ids)<=100 or len(set(ids))!=len(ids)
            or purpose=="writer" and len(ids)!=1):
        raise ValueError("Bounded purpose material required")
    if binding["active_turn"] is not None:
        raise Conflict("Original native turn unsettled")
    system=c.execute("SELECT * FROM hb_context_system WHERE agent=%s AND enabled",(actor["id"],)).fetchone()
    if not system:
        raise Denied("Installed purpose system required")
    host=system["host"]
    lock(c,host)
    kind={"reader":"input","writer":"work","responder":"need"}[purpose]
    states={"reader":("pending",),"writer":("pending","running"),"responder":("ready",)}[purpose]
    interactive=False
    for identity in ids:
        m=c.execute("SELECT * FROM hb_context_material WHERE id=%s AND agent=%s",
                    (identity,actor["id"])).fetchone()
        if not m or m["kind"]!=kind or m["state"] not in states:
            raise Conflict("Original material no longer runnable")
        e=store.readable_event(c,actor,m["source"])
        observation=purpose=="reader" and m["kind"]=="input" and m["value"].get("roomObservation") is True
        store.room(c,actor,e["room"],"can_read" if observation else "can_post")
        d=c.execute("SELECT * FROM hb_delivery WHERE event=%s AND agent=%s",
                    (m["source"],actor["id"])).fetchone()
        if (not d and not observation) or d and d["cancellation_requested"]:
            raise Conflict("Original request cancelled")
        if purpose=="writer" and (d["state"] not in ("claimed","running") or not c.execute(
                "SELECT 1 FROM hb_claim WHERE agent=%s AND delivery=%s AND operation=%s",
                (actor["id"],d["id"],d["claim_id"])).fetchone()):
            raise Conflict("Writer requires original native work claim")
        interactive=interactive or e["data"].get("authorKind")=="human"
    interactive=interactive and bool(c.execute("SELECT 1 FROM hb_host_control_agent WHERE host=%s AND agent=%s",
                                               (host,actor["id"])).fetchone())
    scope={"contextId":str(binding["context_id"]),"nativeThread":p["nativeThread"],"materialIds":ids,
           "roleRevision":system["role_revision"]}
    fingerprint=digest(scope)
    identity=str(uuid5(UUID(str(binding["context_id"])),"inference:"+p["operationId"]))
    old=c.execute("SELECT * FROM hb_context_permit WHERE id=%s FOR UPDATE",(identity,)).fetchone()
    if old and old["fingerprint"]!=fingerprint:
        raise Conflict("Original inference permit changed")
    if old and old["state"]!="queued":
        return {"permit":json_value(old),"admission":{"reason":"original_permit"}}
    other=c.execute("""SELECT id FROM hb_context_permit WHERE agent=%s AND purpose=%s
        AND state IN ('reserved','running')""",(actor["id"],purpose)).fetchone()
    if other:
        raise Conflict("Another original inference permit is unsettled")
    if not old:
        c.execute("""INSERT INTO hb_context_permit
            (id,agent,purpose,host,context_id,native_thread,operation,fingerprint,material_ids,role_revision,queue_class,state)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s::uuid[],%s,%s,'queued')""",
            (identity,actor["id"],purpose,host,binding["context_id"],p["nativeThread"],p["operationId"],
             fingerprint,ids,system["role_revision"],"interactive" if interactive else "worker"))
    c.execute("UPDATE hb_purpose_context SET observed_at=clock_timestamp() WHERE agent=%s AND purpose=%s",
              (actor["id"],purpose))
    policy=c.execute("SELECT * FROM hb_host_admission WHERE host=%s",(host,)).fetchone()
    sample=c.execute("SELECT * FROM hb_host_sample WHERE host=%s",(host,)).fetchone()
    now=c.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
    capacity=None
    if not policy:
        reason="host_policy_missing"
    elif not sample or (now-sample["observed_at"]).total_seconds()>policy["sample_max_age"]:
        reason="host_capacity_stale"
    else:
        counts=resources(c,host)
        free=max(0,(sample["available_memory"]-policy["memory_reserve"])//policy["memory_per_turn"])
        total=min(policy["max_concurrent"],sample["cpu_slots"],counts["total"]+free)
        worker_limit=max(0,total-policy["reserved_interactive"])
        capacity={"total":total,"active":counts["total"],"activeWorkers":counts["workers"],
                  "workerLimit":worker_limit,"policyRevision":policy["revision"]}
        cls="interactive" if interactive else "worker"
        # Queued work from a stopped/revoked purpose cannot hold up another
        # current caller. Presence is per purpose, not shared across an agent.
        head=c.execute("""SELECT q.id FROM hb_context_permit q
            JOIN hb_context_system s ON s.agent=q.agent AND s.enabled AND s.host=q.host
            JOIN hb_purpose_context b ON b.agent=q.agent AND b.purpose=q.purpose
            JOIN hb_token t ON t.digest=b.token_digest AND NOT t.revoked
            JOIN hb_principal a ON a.id=q.agent AND a.active
            JOIN hb_agent_role r ON r.principal=q.agent AND r.revision=q.role_revision
            JOIN hb_placement l ON l.agent=q.agent AND l.host=q.host AND l.enabled
            WHERE q.host=%s AND q.state='queued' AND q.queue_class=%s
              AND b.context_id=q.context_id AND b.native_thread=q.native_thread
              AND b.active_turn IS NULL
              AND NOT EXISTS(SELECT 1 FROM hb_context_permit busy
                WHERE busy.agent=q.agent AND busy.purpose=q.purpose AND busy.state IN ('reserved','running'))
              AND b.observed_at>clock_timestamp()-(%s * interval '1 second')
              AND NOT EXISTS(SELECT 1 FROM unnest(q.material_ids) v(id)
                LEFT JOIN hb_context_material m ON m.id=v.id AND m.agent=q.agent
                LEFT JOIN hb_delivery d ON d.event=m.source AND d.agent=q.agent
                LEFT JOIN hb_event e ON e.id=m.source
                WHERE m.id IS NULL OR (d.id IS NULL AND NOT
                    (q.purpose='reader' AND m.value->>'roomObservation'='true'))
                  OR d.cancellation_requested
                  OR m.kind<>CASE q.purpose WHEN 'reader' THEN 'input' WHEN 'writer' THEN 'work' ELSE 'need' END
                  OR NOT (m.state=ANY(CASE q.purpose WHEN 'reader' THEN ARRAY['pending']
                    WHEN 'writer' THEN ARRAY['pending','running'] ELSE ARRAY['ready'] END))
                  OR (NOT a.administrator AND NOT EXISTS(SELECT 1 FROM hb_membership z
                    WHERE z.room=e.room AND z.principal=q.agent AND z.can_read
                      AND (z.can_post OR q.purpose='reader' AND m.value->>'roomObservation'='true')))
                  OR (NOT a.administrator AND e.audience='direct' AND e.author<>q.agent AND e.recipient<>q.agent))
            ORDER BY q.ticket LIMIT 1""",(host,cls,policy["connector_max_age"])).fetchone()
        if counts["total"]>=total:
            reason="host_capacity_exhausted"
        elif cls=="worker" and counts["workers"]>=worker_limit:
            reason="worker_capacity_reserved_for_control"
        elif not head or str(head["id"])!=identity:
            reason="fair_queue_ahead"
        else:
            reason="eligible"
            c.execute("UPDATE hb_context_permit SET state='reserved' WHERE id=%s",(identity,))
    row=c.execute("SELECT * FROM hb_context_permit WHERE id=%s",(identity,)).fetchone()
    return {"permit":json_value(row),"admission":{"reason":reason,"capacity":capacity}}


def begin(c,actor,purpose,binding,p,ids):
    row=c.execute("SELECT * FROM hb_context_permit WHERE id=%s AND agent=%s AND purpose=%s FOR UPDATE",
                  (p["value"].get("permitId"),actor["id"],purpose)).fetchone()
    if (not row or row["state"]!="reserved" or row["context_id"]!=binding["context_id"]
            or row["native_thread"]!=p["nativeThread"] or [str(i) for i in row["material_ids"]]!=ids):
        raise Conflict("Exact reserved inference permit required")
    c.execute("UPDATE hb_context_permit SET state='running',native_turn=%s WHERE id=%s",
              (p["nativeTurn"],row["id"]))


def end(c,actor,purpose,binding,p):
    identity=p["value"].get("permitId")
    if identity is None:
        # Historical completed receipts predate permits. No capacity may be
        # released based on this compatibility read alone.
        return
    row=c.execute("SELECT * FROM hb_context_permit WHERE id=%s AND agent=%s AND purpose=%s FOR UPDATE",
                  (identity,actor["id"],purpose)).fetchone()
    if (row and row["context_id"]==binding["context_id"] and row["native_thread"]==p["nativeThread"]
            and row["state"]=="released" and row["native_turn"]!=p["nativeTurn"]
            and binding["active_turn"] is None and not binding["active_material"]
            and not c.execute("""SELECT 1 FROM hb_context_operation WHERE agent=%s AND purpose=%s
                AND operation=%s""",(actor["id"],purpose,"context-begin:"+p["nativeTurn"])).fetchone()):
        # The native turn was rejected before business admission after a client
        # reused a stale receipt. Its confirmed terminal fact must not overwrite
        # the older permit or claim another release of its capacity.
        return {"disposition":"not_admitted","capacityReleased":False,
            "referencedPermitId":str(row["id"]),"originalPermitNativeTurn":row["native_turn"]}
    if (not row or row["context_id"]!=binding["context_id"] or row["native_thread"]!=p["nativeThread"]
            or row["native_turn"] not in (None,p["nativeTurn"]) or row["state"] not in ("reserved","running")):
        raise Conflict("Original terminal inference permit changed")
    c.execute("""UPDATE hb_context_permit SET state='released',native_turn=%s,terminal_status=%s
        WHERE id=%s""",(p["nativeTurn"],p["value"]["status"],identity))
    return {"disposition":"admitted","capacityReleased":True,"permitId":str(row["id"])}
