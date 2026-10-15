"""Durable host admission. Unattested executors retain capacity; semantic holds remain explicit.

Host controllers attest resources. Policy and reserved control agents are
administrator-owned. Connectors observe and claim; this module starts no models.
"""

from psycopg.types.json import Jsonb

from .store import Conflict, Denied, digest, json_value

CAPABILITIES = {
    "native-role-goals/v1",
    "cooperation-plan/v1",
    "work-attempts/v1",
    "room-briefing/v1",
    "host-admission/v1",
}
ACTIVE = ("claimed", "running", "unknown", "cancel_requested")


def lock(c, host):
    c.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("admission:" + str(host),))


def host_row(store, c, actor, host):
    row = c.execute(
        "SELECT * FROM hb_host WHERE id=%s AND authority=%s", (host, actor["authority"])
    ).fetchone()
    if not row:
        raise Denied("Host unavailable")
    return row


def command(c, actor, operation, payload):
    c.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
        ("admission-command:" + str(actor["id"]) + ":" + operation,),
    )
    old = c.execute(
        "SELECT * FROM hb_admission_command WHERE principal=%s AND operation=%s",
        (actor["id"], operation),
    ).fetchone()
    fingerprint = digest(payload)
    if old and old["digest"] != fingerprint:
        raise Conflict("Admission operation changed")
    return fingerprint, old["result"] if old else None


def finish(c, actor, operation, fingerprint, value):
    result = json_value({"domain": "host-admission", "operationId": operation, **value})
    c.execute(
        "INSERT INTO hb_admission_command VALUES(%s,%s,%s,%s)",
        (actor["id"], operation, fingerprint, Jsonb(result)),
    )
    return result


def configure(store, actor_id, host, operation, payload):
    with store.pool.connection() as c:
        actor = store.actor(c, actor_id)
        store.admin(actor)
        host_row(store, c, actor, host)
        fingerprint, old = command(c, actor, operation, {"host": host, "policy": payload})
        if old:
            return old
        lock(c, host)
        controller = store.actor(c, payload["controllerId"])
        if controller["authority"] != actor["authority"] or controller["kind"] != "service":
            raise Denied("An enrolled non-model service controller is required")
        prior = c.execute("SELECT * FROM hb_host_admission WHERE host=%s", (host,)).fetchone()
        if (prior["revision"] if prior else 0) != payload["revision"]:
            raise Conflict("Host admission policy revision changed")
        if payload["reservedInteractive"] > payload["maxConcurrent"]:
            raise ValueError("Reserved capacity exceeds total ceiling")
        for identity in payload["controlAgentIds"]:
            agent = store.actor(c, identity)
            if agent["authority"] != actor["authority"] or agent["kind"] != "agent":
                raise Denied("Control agent unavailable")
        c.execute(
            """INSERT INTO hb_host_admission
               (host,authority,controller,machine_identity,max_concurrent,reserved_interactive,
                memory_per_turn,memory_reserve,sample_max_age,connector_max_age)
               VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT(host) DO UPDATE SET controller=EXCLUDED.controller,
                machine_identity=EXCLUDED.machine_identity,max_concurrent=EXCLUDED.max_concurrent,
                reserved_interactive=EXCLUDED.reserved_interactive,memory_per_turn=EXCLUDED.memory_per_turn,
                memory_reserve=EXCLUDED.memory_reserve,sample_max_age=EXCLUDED.sample_max_age,
                connector_max_age=EXCLUDED.connector_max_age,revision=hb_host_admission.revision+1""",
            (
                host,
                actor["authority"],
                controller["id"],
                payload["machineIdentity"],
                payload["maxConcurrent"],
                payload["reservedInteractive"],
                payload["memoryPerTurnBytes"],
                payload["reserveMemoryBytes"],
                payload["sampleMaxAgeSeconds"],
                payload["connectorMaxAgeSeconds"],
            ),
        )
        c.execute("DELETE FROM hb_host_control_agent WHERE host=%s", (host,))
        for identity in payload["controlAgentIds"]:
            c.execute("INSERT INTO hb_host_control_agent VALUES(%s,%s)", (host, identity))
        # A controller/identity change requires fresh attestation, not an old sample.
        if (
            not prior
            or str(prior["controller"]) != payload["controllerId"]
            or prior["machine_identity"] != payload["machineIdentity"]
        ):
            c.execute("DELETE FROM hb_host_sample WHERE host=%s", (host,))
        revision = c.execute(
            "SELECT revision FROM hb_host_admission WHERE host=%s", (host,)
        ).fetchone()["revision"]
        return finish(
            c,
            actor,
            operation,
            fingerprint,
            {"hostId": host, "revision": revision, "accepted": True},
        )


def policy(store, actor_id, host):
    with store.pool.connection() as c:
        actor = store.actor(c, actor_id)
        host_row(store, c, actor, host)
        row = c.execute("SELECT * FROM hb_host_admission WHERE host=%s", (host,)).fetchone()
        if (
            not actor["administrator"]
            and not (row and row["controller"] == actor["id"])
            and not c.execute(
                "SELECT 1 FROM hb_placement WHERE agent=%s AND host=%s",
                (actor["id"], host),
            ).fetchone()
        ):
            raise Denied("Host policy unavailable")
        controls = c.execute(
            "SELECT agent FROM hb_host_control_agent WHERE host=%s ORDER BY agent", (host,)
        ).fetchall()
        return json_value({"policy": row, "controlAgentIds": [r["agent"] for r in controls]})


def sample(store, actor_id, host, operation, payload):
    with store.pool.connection() as c:
        actor = store.actor(c, actor_id)
        host_row(store, c, actor, host)
        row = c.execute("SELECT * FROM hb_host_admission WHERE host=%s", (host,)).fetchone()
        if not row or row["controller"] != actor["id"] or actor["kind"] != "service":
            raise Denied("Only the enrolled host controller may attest capacity")
        fingerprint, old = command(c, actor, operation, {"host": host, "sample": payload})
        if old:
            return old
        lock(c, host)
        row = c.execute("SELECT * FROM hb_host_admission WHERE host=%s", (host,)).fetchone()
        if (
            row["controller"] != actor["id"]
            or payload["machineIdentity"] != row["machine_identity"]
        ):
            raise Conflict("Enrolled host controller or machine identity changed")
        result = c.execute(
            """INSERT INTO hb_host_sample(host,cpu_slots,available_memory)
               VALUES(%s,%s,%s) ON CONFLICT(host) DO UPDATE SET
               cpu_slots=EXCLUDED.cpu_slots,available_memory=EXCLUDED.available_memory,
               observed_at=clock_timestamp() RETURNING observed_at""",
            (host, payload["cpuSlots"], payload["availableMemoryBytes"]),
        ).fetchone()
        return finish(
            c,
            actor,
            operation,
            fingerprint,
            {"hostId": host, "observedAt": result["observed_at"], "accepted": True},
        )


def presence(store, c, actor, host, capabilities):
    placement = c.execute(
        "SELECT * FROM hb_placement WHERE agent=%s AND host=%s",
        (actor["id"], host),
    ).fetchone()
    if actor["kind"] != "agent" or not placement:
        raise Denied("Connector not enrolled")
    c.execute(
        """INSERT INTO hb_connector_observation(agent,host,capabilities)
           VALUES(%s,%s,%s) ON CONFLICT(agent) DO UPDATE SET host=EXCLUDED.host,
           capabilities=EXCLUDED.capabilities,observed_at=clock_timestamp()""",
        (actor["id"], host, Jsonb(sorted(set(capabilities)))),
    )
    return placement


def diagnose(store, c, actor, host, capabilities, record=False):
    """A current observation. Claim calls hold the host mutex and recheck ownership."""
    now = c.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
    placement = c.execute(
        "SELECT * FROM hb_placement WHERE agent=%s AND host=%s", (actor["id"], host)
    ).fetchone()
    own = c.execute(
        """SELECT d.id,e.created_at FROM hb_delivery d JOIN hb_event e ON e.id=d.event
           WHERE d.agent=%s AND d.state='pending'
             AND (%s OR EXISTS(SELECT 1 FROM hb_membership m
               WHERE m.room=e.room AND m.principal=%s AND m.can_read))
           ORDER BY e.seq LIMIT 1""",
        (actor["id"], actor["administrator"], actor["id"]),
    ).fetchone()
    busy = c.execute(
        """SELECT d.id,d.state,d.native_thread,d.native_turn,x.state AS resource_state
           FROM hb_delivery d LEFT JOIN hb_executor x ON x.delivery=d.id AND x.state<>'stopped'
           WHERE d.agent=%s AND (d.state IN ('claimed','running','unknown','cancel_requested')
             OR x.id IS NOT NULL)
           ORDER BY d.updated_at LIMIT 1""",
        (actor["id"],),
    ).fetchone()
    result = {
        "hostId": str(host),
        "eligibleDeliveryId": None,
        "pending": own,
        "busy": busy,
        "enabled": bool(placement and placement["enabled"]),
        "reason": None,
        "observedAt": now,
        "capacity": None,
    }
    policy = c.execute("SELECT * FROM hb_host_admission WHERE host=%s", (host,)).fetchone()
    observation = c.execute("SELECT * FROM hb_host_sample WHERE host=%s", (host,)).fetchone()
    reason = None
    if not placement or not placement["enabled"]:
        reason = "placement_disabled"
    elif busy:
        reason = (
            "unresolved_execution"
            if busy["state"] == "unknown"
            else "executor_not_drained"
            if busy["state"] in ("completed", "failed", "cancelled")
            else "agent_busy"
        )
    elif not own:
        reason = "no_pending_work"
    elif not CAPABILITIES <= set(capabilities):
        reason = "connector_upgrade_required"
    elif not policy:
        reason = "host_policy_missing"
    elif (
        not observation
        or (now - observation["observed_at"]).total_seconds() > policy["sample_max_age"]
    ):
        reason = "host_capacity_stale"
    else:
        counts = c.execute(
            """SELECT count(*) AS total,
               count(*) FILTER (WHERE COALESCE(t.queue_class,'worker')='worker') AS workers
               FROM (
                 SELECT x.delivery FROM hb_executor x WHERE x.host=%s AND x.state<>'stopped'
                 UNION ALL
                 SELECT d.id FROM hb_delivery d WHERE d.host=%s
                   AND d.state IN ('claimed','running','unknown','cancel_requested')
                   AND NOT EXISTS(SELECT 1 FROM hb_executor x WHERE x.delivery=d.id AND x.generation=d.generation)
               ) resources
               LEFT JOIN hb_admission_trace t ON t.delivery=resources.delivery""",
            (host, host),
        ).fetchone()
        free = max(
            0,
            (observation["available_memory"] - policy["memory_reserve"])
            // policy["memory_per_turn"],
        )
        total = min(policy["max_concurrent"], observation["cpu_slots"], counts["total"] + free)
        workers = max(0, total - policy["reserved_interactive"])
        result["capacity"] = {
            "total": total,
            "workerLimit": workers,
            "active": counts["total"],
            "activeWorkers": counts["workers"],
            "policyRevision": policy["revision"],
            "sampleObservedAt": observation["observed_at"],
        }
        # Choose one head per available connector; stale, revoked, paused and
        # incompatible connectors cannot block unrelated ready rooms.
        rows = c.execute(
            """WITH heads AS (
                SELECT d.id,d.agent,e.room,e.seq,
                  CASE WHEN h.agent IS NOT NULL AND e.data->>'authorKind'='human'
                    THEN 'interactive' ELSE 'worker' END AS queue_class,
                  row_number() OVER(PARTITION BY d.agent ORDER BY
                    CASE WHEN h.agent IS NOT NULL AND e.data->>'authorKind'='human' THEN 0 ELSE 1 END,e.seq) AS position
                FROM hb_delivery d JOIN hb_event e ON e.id=d.event
                JOIN hb_principal p ON p.id=d.agent AND p.active
                JOIN hb_placement l ON l.agent=d.agent AND l.host=%s AND l.enabled
                JOIN hb_connector_observation o ON o.agent=d.agent AND o.host=l.host
                LEFT JOIN hb_host_control_agent h ON h.host=l.host AND h.agent=d.agent
                LEFT JOIN hb_work w ON w.delivery=d.id LEFT JOIN hb_goal g ON g.id=w.goal
                WHERE d.state='pending' AND (g.id IS NULL OR g.state='active')
                  AND o.observed_at>clock_timestamp()-(%s * interval '1 second')
                  AND o.capabilities @> %s
                  AND (p.administrator OR EXISTS(SELECT 1 FROM hb_membership m
                    WHERE m.room=e.room AND m.principal=p.id AND m.can_read AND m.can_post))
                  AND NOT EXISTS(SELECT 1 FROM hb_delivery b WHERE b.agent=d.agent
                    AND b.state IN ('claimed','running','unknown','cancel_requested'))
                  AND NOT EXISTS(SELECT 1 FROM hb_executor x WHERE x.agent=d.agent AND x.state<>'stopped')
            ) SELECT * FROM heads WHERE position=1""",
            (host, policy["connector_max_age"], Jsonb(sorted(CAPABILITIES))),
        ).fetchall()
        own_head = next((r for r in rows if r["agent"] == actor["id"]), None)
        if not own_head:
            reason = "work_or_permission_not_ready"
        else:
            result["pending"] = {"id": own_head["id"]}
            result["queueClass"] = own_head["queue_class"]
            if counts["total"] >= total:
                reason = "host_capacity_exhausted"
            elif own_head["queue_class"] == "worker" and counts["workers"] >= workers:
                reason = "worker_capacity_reserved_for_control"
            else:
                history = c.execute(
                    """SELECT e.room,t.queue_class,max(t.ticket) AS last_ticket
                       FROM hb_admission_trace t JOIN hb_delivery d ON d.id=t.delivery
                       JOIN hb_event e ON e.id=d.event WHERE t.host=%s
                       GROUP BY e.room,t.queue_class""",
                    (host,),
                ).fetchall()
                last = {(r["room"], r["queue_class"]): r["last_ticket"] or 0 for r in history}
                candidates = [r for r in rows if r["queue_class"] == own_head["queue_class"]]
                selected = min(
                    candidates, key=lambda r: (last.get((r["room"], r["queue_class"]), 0), r["seq"])
                )
                if selected["agent"] != actor["id"]:
                    reason = "fair_queue_ahead"
                else:
                    result["eligibleDeliveryId"] = own_head["id"]
    result["reason"] = reason or "eligible"
    if record:
        record_observation(c, host, result)
    return result


def record_observation(c, host, result):
    if not result["pending"]:
        return
    c.execute(
        """UPDATE hb_admission_trace SET host=%s,
          queue_class=COALESCE(%s,queue_class),
          first_eligible_at=CASE WHEN %s='eligible'
            THEN COALESCE(first_eligible_at,clock_timestamp()) ELSE first_eligible_at END,
          blocker_since=CASE WHEN blocker IS DISTINCT FROM %s THEN clock_timestamp()
            ELSE blocker_since END, blocker=%s,observed_at=clock_timestamp()
          WHERE delivery=%s""",
        (
            host,
            result.get("queueClass"),
            result["reason"],
            result["reason"],
            result["reason"],
            result["pending"]["id"],
        ),
    )


def observe(store, actor_id, host, capabilities):
    with store.pool.connection() as c:
        actor = store.actor(c, actor_id)
        presence(store, c, actor, host, capabilities)
        return json_value(diagnose(store, c, actor, host, capabilities, record=True))


def reserve(c, host, identity, queue_class):
    ticket = c.execute(
        "UPDATE hb_host_admission SET next_ticket=next_ticket+1 WHERE host=%s RETURNING next_ticket",
        (host,),
    ).fetchone()["next_ticket"]
    c.execute(
        "UPDATE hb_admission_trace SET host=%s,queue_class=%s,ticket=%s WHERE delivery=%s",
        (host, queue_class, ticket, identity),
    )


def trace(c, identity):
    return c.execute("SELECT * FROM hb_admission_trace WHERE delivery=%s", (identity,)).fetchone()
