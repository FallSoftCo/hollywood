"""Durable goals, dependency plans and evidence-based work acceptance.

No models, filesystem access or autonomous timers live here. Review admission is
a bounded, explicit goal policy, keyed by external worker evidence/state.
"""

from .store import Conflict, Denied, digest, json_value, uid
from psycopg.types.json import Jsonb

TERMINAL = {"completed", "failed", "cancelled"}


def goal_row(store, c, actor, identity, lock=False):
    row = c.execute(
        "SELECT * FROM hb_goal WHERE id=%s AND authority=%s"
        + (" FOR UPDATE" if lock is True else " FOR SHARE" if lock == "share" else ""),
        (identity, actor["authority"]),
    ).fetchone()
    if not row:
        raise Denied("Goal unavailable")
    store.room(c, actor, row["room"])
    store.readable_event(c, actor, row["source"])
    return row


def manage(actor, goal):
    if not actor["administrator"] and actor["id"] not in (goal["owner"], goal["creator"]):
        raise Denied("Accountable goal owner required")


def work_row(store, c, actor, identity):
    row = c.execute(
        "SELECT * FROM hb_work WHERE id=%s AND authority=%s", (identity, actor["authority"])
    ).fetchone()
    if not row:
        raise Denied("Work unavailable")
    store.room(c, actor, row["room"])
    if row["request_event"]:
        store.readable_event(c, actor, row["request_event"])
    return row


def snapshot(store, c, actor, goal):
    # The plan is the goal's revisioned set of work records and immutable edges.
    # Unreadable work is never included; neither visibility nor membership admits it.
    rows = c.execute(
        """SELECT w.*,d.state AS execution_state,d.native_thread,d.native_turn,d.host
           FROM hb_work w LEFT JOIN hb_delivery d ON d.id=w.delivery
           WHERE w.goal=%s AND (%s OR EXISTS(
             SELECT 1 FROM hb_membership m WHERE m.room=w.room AND m.principal=%s AND m.can_read))
           ORDER BY w.id LIMIT 101""",
        (goal["id"], actor["administrator"], actor["id"]),
    ).fetchall()
    works = []
    for row in rows[:100]:
        if row["request_event"]:
            store.readable_event(c, actor, row["request_event"])
        deps = c.execute(
            "SELECT prerequisite FROM hb_work_dependency WHERE work=%s ORDER BY prerequisite",
            (row["id"],),
        ).fetchall()
        works.append({**row, "dependencies": [r["prerequisite"] for r in deps]})
    return json_value({"goal": goal, "plan": works, "planTruncated": len(rows) > 100})


def read(store, actor_id, identity, work=False):
    with store.pool.connection() as c:
        a = store.actor(c, actor_id)
        if work:
            w = work_row(store, c, a, identity)
            c.execute("SELECT id FROM hb_goal WHERE id=%s FOR SHARE", (w["goal"],))
            w = work_row(store, c, a, identity)
            d = c.execute("SELECT * FROM hb_delivery WHERE id=%s", (w["delivery"],)).fetchone()
            deps = c.execute(
                "SELECT prerequisite FROM hb_work_dependency WHERE work=%s ORDER BY prerequisite",
                (identity,),
            ).fetchall()
            return json_value(
                {"work": w, "execution": d, "dependencies": [r["prerequisite"] for r in deps]}
            )
        return snapshot(store, c, a, goal_row(store, c, a, identity, lock="share"))


def listing(store, actor_id, room, after, limit):
    with store.pool.connection() as c:
        a = store.actor(c, actor_id)
        store.room(c, a, room)
        rows = c.execute(
            """SELECT g.* FROM hb_goal g JOIN hb_event e ON e.id=g.source
               WHERE g.room=%s AND (%s OR e.audience='room' OR e.author=%s OR e.recipient=%s)
               AND (%s::uuid IS NULL OR g.id>%s::uuid) ORDER BY g.id LIMIT %s""",
            (room, a["administrator"], a["id"], a["id"], after, after, limit + 1),
        ).fetchall()
        return json_value(
            {
                "goals": rows[:limit],
                "nextCursor": rows[limit - 1]["id"] if len(rows) > limit else None,
            }
        )


def mutation(store, actor_id, action, identity, operation, payload):
    fingerprint = digest({"workflow": action, "identity": identity, **payload})
    with store.pool.connection() as c:
        a = store.actor(c, actor_id)
        c.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            (str(a["id"]) + ":" + operation,),
        )
        old = c.execute(
            "SELECT * FROM hb_command WHERE principal=%s AND operation=%s", (a["id"], operation)
        ).fetchone()
        if old:
            if old["digest"] != fingerprint:
                raise Conflict("Workflow operation changed")
            event = store.readable_event(c, a, old["event"])
            return event["data"]["workflowReceipt"]
        if action == "goal.create":
            room = store.room(c, a, identity, "can_request")
            source = store.readable_event(c, a, payload["sourceEventId"])
            if source["kind"] != "request" or str(source["room"]) != identity:
                raise Denied("Goal requires an addressed source request in its room")
            # Cross-room private delegation needs an explicit sharing contract.
            # It must never be inferred from an administrator's ability to read.
            if source["audience"] != "room":
                raise Denied("Room-visible goal requires room-visible source")
            from .attention import human_owner

            human_owner(c, source)
            owner = c.execute(
                "SELECT * FROM hb_principal WHERE id=%s AND authority=%s AND kind='agent' AND active",
                (room["owner"], a["authority"]),
            ).fetchone()
            if not owner:
                raise Denied("Accountable agent owner unavailable")
            gid = payload["id"]
            c.execute(
                """INSERT INTO hb_goal(id,authority,room,owner,creator,source,title,objective,acceptance,review_remaining)
                   VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    gid,
                    a["authority"],
                    identity,
                    owner["id"],
                    a["id"],
                    source["id"],
                    payload["title"],
                    payload["objective"],
                    payload["acceptance"],
                    payload["reviewBudget"],
                ),
            )
            goal = goal_row(store, c, a, gid, True)
            target_room, work = identity, None
        else:
            if action.startswith("work."):
                work = work_row(store, c, a, identity)
                # Workers need only the assigned room, never the private Global room.
                goal = c.execute(
                    "SELECT * FROM hb_goal WHERE id=%s AND authority=%s FOR UPDATE",
                    (work["goal"], a["authority"]),
                ).fetchone()
                work = work_row(store, c, a, identity)
                if work["revision"] != payload["revision"]:
                    raise Conflict("Work revision changed")
                target_room = str(work["room"])
            else:
                goal = goal_row(store, c, a, identity, True)
                work = None
                target_room = str(goal["room"])
                if goal["revision"] != payload["revision"]:
                    raise Conflict("Goal/plan revision changed")
            if action != "work.report":
                manage(a, goal)
                goal_row(store, c, a, str(goal["id"]))
            if (
                action not in ("goal.state", "work.report", "work.review", "work.cancel")
                and goal["state"] != "active"
            ):
                raise Conflict("Goal is not active")
            if action == "goal.work":
                store.room(c, a, payload["roomId"], "can_request")
                owner = c.execute(
                    "SELECT * FROM hb_principal WHERE id=%s AND authority=%s AND active AND kind='agent'",
                    (payload["ownerId"], a["authority"]),
                ).fetchone()
                if not owner:
                    raise Denied("Work owner unavailable")
                store.room(c, owner, payload["roomId"])
                for dependency in payload["dependencies"]:
                    if not c.execute(
                        "SELECT 1 FROM hb_work WHERE id=%s AND goal=%s", (dependency, goal["id"])
                    ).fetchone():
                        raise Denied("Prerequisite is outside this goal")
                    # Don't disclose a hidden prerequisite through a delegated plan.
                    prior = work_row(store, c, a, dependency)
                    store.room(c, owner, prior["room"])
                wid = payload["id"]
                c.execute(
                    """INSERT INTO hb_work(id,authority,goal,room,owner,specification,acceptance)
                       VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        wid,
                        a["authority"],
                        goal["id"],
                        payload["roomId"],
                        payload["ownerId"],
                        payload["specification"],
                        payload["acceptance"],
                    ),
                )
                for dep in payload["dependencies"]:
                    c.execute(
                        "INSERT INTO hb_work_dependency VALUES(%s,%s,%s,%s)",
                        (a["authority"], goal["id"], wid, dep),
                    )
                work = work_row(store, c, a, wid)
                target_room = payload["roomId"]
            elif action == "work.assign":
                if work["state"] != "planned" or work["delivery"]:
                    raise Conflict("Work already admitted or no longer planned")
                if c.execute(
                    """SELECT 1 FROM hb_work_dependency x JOIN hb_work p ON p.id=x.prerequisite
                       WHERE x.work=%s AND p.state<>'accepted' LIMIT 1""",
                    (identity,),
                ).fetchone():
                    raise Conflict("Prerequisites have not been accepted")
                store.room(c, a, work["room"], "can_request")
                target = store.actor(c, work["owner"])
                store.room(c, target, work["room"])
                if target["kind"] != "agent":
                    raise Denied("Execution owner is not an agent")
                request = store.event(
                    c,
                    a,
                    work["room"],
                    "request",
                    work["specification"],
                    recipient=work["owner"],
                    source=goal["source"],
                    data={
                        "goalId": str(goal["id"]),
                        "workId": identity,
                        "acceptance": work["acceptance"],
                        "workRevision": work["revision"] + 1,
                    },
                )
                delivery = uid()
                c.execute(
                    "INSERT INTO hb_delivery(id,authority,event,agent) VALUES(%s,%s,%s,%s)",
                    (delivery, a["authority"], request, work["owner"]),
                )
                c.execute(
                    "UPDATE hb_work SET state='assigned',request_event=%s,delivery=%s,revision=revision+1 WHERE id=%s",
                    (request, delivery, identity),
                )
            elif action == "work.report":
                if a["id"] != work["owner"] or not work["delivery"]:
                    raise Denied("Assigned worker required")
                d = c.execute(
                    "SELECT * FROM hb_delivery WHERE id=%s FOR SHARE", (work["delivery"],)
                ).fetchone()
                if (
                    d["state"] not in ("claimed", "running")
                    or d["generation"] != payload["generation"]
                    or d["claim_id"] != payload["claimId"]
                ):
                    raise Conflict("Report lacks current execution ownership")
                if work["state"] not in ("assigned", "blocked", "submitted"):
                    raise Conflict("Work no longer accepts a report")
                if any(e["hostId"] != str(d["host"]) for e in payload["evidence"]):
                    raise Denied("Artifact host differs from execution host")
                if payload["state"] == "submitted" and not payload["evidence"]:
                    raise Conflict("Submitted work requires evidence")
                c.execute(
                    "UPDATE hb_work SET state=%s,evidence=%s,note=%s,revision=revision+1 WHERE id=%s",
                    (payload["state"], Jsonb(payload["evidence"]), payload["note"], identity),
                )
            elif action == "work.review":
                if work["state"] != "submitted":
                    raise Conflict("Only submitted work can be reviewed")
                d = c.execute(
                    "SELECT * FROM hb_delivery WHERE id=%s FOR SHARE", (work["delivery"],)
                ).fetchone()
                if d["state"] != "completed":
                    raise Conflict("Native execution has not completed")
                if a["id"] == work["owner"] and not a["administrator"]:
                    raise Denied("Worker cannot accept its own work")
                c.execute(
                    "UPDATE hb_work SET state=%s,note=%s,revision=revision+1 WHERE id=%s",
                    (payload["decision"], payload["note"], identity),
                )
            elif action == "work.cancel":
                if work["state"] in ("accepted", "cancelled"):
                    raise Conflict("Terminal work cannot be cancelled again")
                d = c.execute(
                    "SELECT * FROM hb_delivery WHERE id=%s FOR UPDATE", (work["delivery"],)
                ).fetchone()
                state = "cancelled"
                if d and d["state"] in ("claimed", "running", "unknown", "cancel_requested"):
                    state = "cancel_requested"
                    if d["state"] in ("claimed", "running"):
                        c.execute(
                            "UPDATE hb_delivery SET state='cancel_requested',revision=revision+1,updated_at=clock_timestamp() WHERE id=%s",
                            (d["id"],),
                        )
                elif d and d["state"] == "pending":
                    c.execute(
                        "UPDATE hb_delivery SET state='cancelled',revision=revision+1,updated_at=clock_timestamp() WHERE id=%s",
                        (d["id"],),
                    )
                c.execute(
                    "UPDATE hb_work SET state=%s,note=%s,revision=revision+1 WHERE id=%s",
                    (state, payload["note"], identity),
                )
            elif action == "goal.state":
                state = payload["state"]
                if goal["state"] in ("accepted", "cancelled") and state != goal["state"]:
                    raise Conflict("Terminal goal cannot silently restart")
                if state == "accepted":
                    rows = c.execute(
                        "SELECT state,room FROM hb_work WHERE goal=%s", (goal["id"],)
                    ).fetchall()
                    for item in rows:
                        store.room(c, a, item["room"])
                    if not rows or any(w["state"] != "accepted" for w in rows):
                        raise Conflict("Goal has unaccepted work")
                    if not payload["note"].strip():
                        raise Conflict("Goal acceptance requires explicit review")
                if (
                    state == "cancelled"
                    and c.execute(
                        """SELECT 1 FROM hb_work w JOIN hb_delivery d ON d.id=w.delivery WHERE w.goal=%s
                       AND d.state IN ('claimed','running','unknown','cancel_requested') LIMIT 1""",
                        (goal["id"],),
                    ).fetchone()
                ):
                    raise Conflict("Admitted work requires cancellation reconciliation first")
                if state == "cancelled":
                    for item in c.execute(
                        "SELECT room FROM hb_work WHERE goal=%s", (goal["id"],)
                    ).fetchall():
                        store.room(c, a, item["room"])
                    c.execute(
                        "UPDATE hb_delivery SET state='cancelled',revision=revision+1 WHERE state='pending' AND id IN (SELECT delivery FROM hb_work WHERE goal=%s)",
                        (goal["id"],),
                    )
                    c.execute(
                        "UPDATE hb_work SET state='cancelled',revision=revision+1 WHERE goal=%s AND state<>'accepted'",
                        (goal["id"],),
                    )
                c.execute("UPDATE hb_goal SET state=%s WHERE id=%s", (state, goal["id"]))
                # Pause is admission control, not a claim that active effects stopped.
            else:
                raise ValueError("Unknown workflow operation")
            c.execute("UPDATE hb_goal SET revision=revision+1 WHERE id=%s", (goal["id"],))
            goal = c.execute("SELECT * FROM hb_goal WHERE id=%s", (goal["id"],)).fetchone()
        if work:
            work = c.execute("SELECT * FROM hb_work WHERE id=%s", (work["id"],)).fetchone()
        receipt = json_value(
            {
                "accepted": True,
                "goalId": goal["id"],
                "goalRevision": goal["revision"],
                "workId": work["id"] if work else None,
                "workRevision": work["revision"] if work else None,
                "state": work["state"] if work else goal["state"],
                "requestEventId": work["request_event"] if work else None,
                "deliveryId": work["delivery"] if work else None,
            }
        )
        event = store.event(
            c,
            a,
            target_room,
            "control" if action == "goal.state" else "progress",
            action
            + ": "
            + (
                payload.get("note")
                or payload.get("title")
                or (work["state"] if work else goal["state"])
            ),
            source=work["request_event"] if work and work["request_event"] else goal["source"],
            data={
                "workflowAction": action,
                "goalId": str(goal["id"]),
                "workId": str(work["id"]) if work else None,
                "workflowReceipt": receipt,
            },
        )
        receipt["eventId"] = event
        c.execute(
            "UPDATE hb_event SET data=jsonb_set(data,'{workflowReceipt}',%s) WHERE id=%s",
            (Jsonb(receipt), event),
        )
        c.execute(
            "INSERT INTO hb_command VALUES(%s,%s,%s,%s,%s)",
            (a["authority"], a["id"], operation, fingerprint, event),
        )
        if action == "work.report":
            c.execute("UPDATE hb_work SET report_event=%s WHERE id=%s", (event, work["id"]))
        return receipt


def dispatch_reviews(store, actor_id, host):
    """Desired-state reconciliation keyed by material worker reports; no timed goals."""
    with store.pool.connection() as c:
        a = store.actor(c, actor_id)
        if (
            a["kind"] != "agent"
            or not c.execute(
                "SELECT 1 FROM hb_placement WHERE agent=%s AND host=%s AND enabled",
                (actor_id, host),
            ).fetchone()
        ):
            raise Denied("Enrolled execution owner required")
        goals = c.execute(
            "SELECT * FROM hb_goal WHERE owner=%s AND state='active' AND review_remaining>0 ORDER BY id LIMIT 16 FOR UPDATE SKIP LOCKED",
            (actor_id,),
        ).fetchall()
        admitted = []
        for goal in goals:
            store.room(c, a, goal["room"], "can_request")
            if (
                goal["review_request"]
                and c.execute(
                    "SELECT 1 FROM hb_delivery WHERE event=%s AND state NOT IN ('completed','failed','cancelled')",
                    (goal["review_request"],),
                ).fetchone()
            ):
                continue
            material = c.execute(
                """SELECT w.id,w.report_event,w.owner,d.state,d.revision,e.seq
                   FROM hb_work w JOIN hb_delivery d ON d.id=w.delivery
                   JOIN hb_event e ON e.id=w.report_event
                   WHERE w.goal=%s AND w.owner<>%s AND w.state IN ('submitted','blocked')
                   AND d.state IN ('completed','failed','cancelled','unknown') ORDER BY w.id""",
                (goal["id"], actor_id),
            ).fetchall()
            if not material:
                continue
            key = digest(json_value(material))
            if c.execute(
                "SELECT 1 FROM hb_goal_review WHERE goal=%s AND trigger_digest=%s",
                (goal["id"], key),
            ).fetchone():
                continue
            source = max(material, key=lambda x: x["seq"])["report_event"]
            store.readable_event(c, a, source)
            request = store.event(
                c,
                a,
                goal["room"],
                "request",
                "Review material worker reports for goal "
                + str(goal["id"])
                + ". Read its current plan, inspect execution and artifact evidence, then explicitly accept, reject or report a blocker. This bounded review does not authorize new unrelated work.",
                recipient=actor_id,
                source=source,
                data={
                    "goalId": str(goal["id"]),
                    "goalReview": True,
                    "policy": "bounded-worker-report",
                    "triggerDigest": key,
                },
            )
            c.execute(
                "INSERT INTO hb_delivery(id,authority,event,agent) VALUES(%s,%s,%s,%s)",
                (uid(), a["authority"], request, actor_id),
            )
            c.execute("INSERT INTO hb_goal_review VALUES(%s,%s,%s)", (goal["id"], key, request))
            c.execute(
                "UPDATE hb_goal SET review_remaining=review_remaining-1,review_digest=%s,review_request=%s,revision=revision+1 WHERE id=%s",
                (key, request, goal["id"]),
            )
            admitted.append({"goalId": str(goal["id"]), "requestEventId": request})
        return {"admitted": admitted, "modelsStarted": 0}


def supervision(store, actor_id, after, limit):
    with store.pool.connection() as c:
        a = store.actor(c, actor_id)
        stored = c.execute(
            "SELECT * FROM hb_supervision_cursor WHERE principal=%s", (actor_id,)
        ).fetchone()
        start = after if after is not None else stored["seq"] if stored else 0
        head = c.execute(
            "SELECT next_seq FROM hb_authority WHERE id=%s", (a["authority"],)
        ).fetchone()["next_seq"]
        if start > head:
            raise Conflict("Supervision cursor exceeds history")
        rows = c.execute(
            """SELECT e.* FROM hb_event e WHERE e.authority=%s AND e.seq>%s AND e.seq<=%s
               AND (%s OR EXISTS(SELECT 1 FROM hb_membership m WHERE m.room=e.room AND m.principal=%s AND m.can_read))
               AND (%s OR e.audience='room' OR e.author=%s OR e.recipient=%s)
               ORDER BY e.seq LIMIT %s""",
            (
                a["authority"],
                start,
                head,
                a["administrator"],
                actor_id,
                a["administrator"],
                actor_id,
                actor_id,
                limit,
            ),
        ).fetchall()
        return json_value(
            {
                "events": [
                    {
                        "eventId": e["id"],
                        "sequence": e["seq"],
                        "roomId": e["room"],
                        "kind": e["kind"],
                        "material": bool(
                            e["data"].get("workflowAction")
                            or e["kind"] in ("question", "approval", "result")
                        ),
                        "goalId": e["data"].get("goalId"),
                        "workId": e["data"].get("workId"),
                    }
                    for e in rows
                ],
                "nextCursor": rows[-1]["seq"] if len(rows) == limit else head,
                "head": head,
                "cursorRevision": stored["revision"] if stored else 0,
            }
        )


def acknowledge(store, actor_id, seq, revision):
    with store.pool.connection() as c:
        a = store.actor(c, actor_id)
        c.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("supervision:" + actor_id,)
        )
        old = c.execute(
            "SELECT * FROM hb_supervision_cursor WHERE principal=%s FOR UPDATE", (actor_id,)
        ).fetchone()
        head = c.execute(
            "SELECT next_seq FROM hb_authority WHERE id=%s", (a["authority"],)
        ).fetchone()["next_seq"]
        if seq > head or old and seq < old["seq"]:
            raise Conflict("Cursor may not skip future or rewind history")
        if old and old["seq"] == seq:
            return json_value(old)
        if (old["revision"] if old else 0) != revision:
            raise Conflict("Supervision cursor changed")
        return json_value(
            c.execute(
                """INSERT INTO hb_supervision_cursor(authority,principal,seq) VALUES(%s,%s,%s)
               ON CONFLICT(principal) DO UPDATE SET seq=EXCLUDED.seq,revision=hb_supervision_cursor.revision+1 RETURNING *""",
                (a["authority"], actor_id, seq),
            ).fetchone()
        )


def claim_candidate(store, c, actor):
    # Lock a goal before its delivery, matching goal/work lifecycle mutations.
    # Observation is a hint; this is the authoritative admission recheck.
    candidate = c.execute(
        """SELECT d.id,w.goal FROM hb_delivery d JOIN hb_event e ON e.id=d.event
           LEFT JOIN hb_work w ON w.delivery=d.id LEFT JOIN hb_goal g ON g.id=w.goal
           WHERE d.agent=%s AND d.state='pending' AND (g.id IS NULL OR g.state='active')
           ORDER BY e.seq LIMIT 1""",
        (actor["id"],),
    ).fetchone()
    if not candidate:
        return None
    if candidate["goal"]:
        goal = c.execute(
            "SELECT state FROM hb_goal WHERE id=%s FOR SHARE", (candidate["goal"],)
        ).fetchone()
        if not goal or goal["state"] != "active":
            return None
    return c.execute(
        "SELECT * FROM hb_delivery WHERE id=%s AND agent=%s AND state='pending' FOR UPDATE SKIP LOCKED",
        (candidate["id"], actor["id"]),
    ).fetchone()
