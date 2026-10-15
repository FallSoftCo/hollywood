"""Versioned explicit decisions and bounded, permission-filtered work briefings.

History is evidence; these rows identify current direction. Reads never dispatch
work, and source content remains tool data rather than privileged role metadata.
"""

from psycopg.types.json import Jsonb

from .store import Conflict, Denied, digest, json_value, uid


def authorized_goal(store, c, actor, identity, room):
    from .work import goal_row

    goal = goal_row(store, c, actor, identity, lock="share")
    if goal["room"] != room["id"]:
        raise Denied("Decision scope must belong to this room")
    return goal


def write(store, actor_id, room_id, operation, payload):
    fingerprint = digest({"decisionRoom": room_id, **payload})
    with store.pool.connection() as c:
        actor = store.actor(c, actor_id)
        room = store.room(c, actor, room_id, "can_request")
        c.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            (str(actor["id"]) + ":" + operation,),
        )
        prior = c.execute(
            "SELECT * FROM hb_command WHERE principal=%s AND operation=%s",
            (actor["id"], operation),
        ).fetchone()
        if prior:
            if prior["digest"] != fingerprint:
                raise Conflict("Decision operation changed")
            return store.readable_event(c, actor, prior["event"])["data"]["decisionReceipt"]
        # Serialize creation and changes of one semantic key, not the whole room.
        c.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            ("decision:" + room_id + ":" + payload["key"],),
        )
        old = c.execute(
            "SELECT * FROM hb_decision WHERE room=%s AND key=%s FOR UPDATE",
            (room_id, payload["key"]),
        ).fetchone()
        if old and (
            str(old["id"]) != payload["id"]
            or (str(old["goal"]) if old["goal"] else None) != payload["goalId"]
        ):
            raise Conflict("Decision identity or scope changed")
        if (old["revision"] if old else 0) != payload["revision"]:
            raise Conflict("Decision revision changed")
        goal = (
            authorized_goal(store, c, actor, payload["goalId"], room)
            if payload["goalId"] else None
        )
        managers = {room["owner"]}
        if old:
            managers.add(old["creator"])
        if goal:
            managers.update((goal["owner"], goal["creator"]))
        if not actor["administrator"] and actor["id"] not in managers:
            # A human with request rights may establish a new explicit decision.
            # Other humans cannot silently replace it; the owner can arbitrate.
            if old or actor["kind"] != "human":
                raise Denied("Accountable decision issuer or scope owner required")
        source = store.readable_event(c, actor, payload["sourceEventId"])
        if source["room"] != room["id"] or source["audience"] != "room":
            raise Denied("Room decision requires room-visible evidence in its scope")
        if (
            not actor["administrator"]
            and source["author"] != actor["id"]
            and source["recipient"] != actor["id"]
        ):
            raise Denied("Decision evidence must belong to the addressed exchange")
        if not old:
            c.execute(
                "INSERT INTO hb_decision(id,authority,room,goal,key,creator) VALUES(%s,%s,%s,%s,%s,%s)",
                (payload["id"], actor["authority"], room["id"], payload["goalId"],
                 payload["key"], actor["id"]),
            )
        # Match goal -> decision-scope -> authority lock order used by work
        # reporting/review. A room decision cannot race acceptance's freshness check.
        c.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            ("decision-scope:" + room_id,),
        )
        revision = payload["revision"] + 1
        event = store.event(
            c, actor, room["id"], "progress",
            "decision." + payload["state"] + ": " + payload["key"],
            source=source["id"],
            data={"type": "decisionState", "decisionId": payload["id"],
                  "decisionRevision": revision, "goalId": payload["goalId"]},
        )
        c.execute(
            """INSERT INTO hb_decision_revision(decision,revision,state,body,issuer,source,event)
               VALUES(%s,%s,%s,%s,%s,%s,%s)""",
            (payload["id"], revision, payload["state"], payload["body"],
             actor["id"], source["id"], event),
        )
        c.execute(
            "UPDATE hb_decision SET revision=%s WHERE id=%s", (revision, payload["id"])
        )
        receipt = json_value({
            "accepted": True, "decisionId": payload["id"], "revision": revision,
            "state": payload["state"], "eventId": event,
        })
        c.execute(
            "UPDATE hb_event SET data=data || %s WHERE id=%s",
            (Jsonb({"decisionReceipt": receipt}), event),
        )
        c.execute(
            "INSERT INTO hb_command VALUES(%s,%s,%s,%s,%s)",
            (actor["authority"], actor["id"], operation, fingerprint, event),
        )
        return receipt


def page(store, c, actor, room_id, after, limit, goal=None, scope_only=False):
    room = store.room(c, actor, room_id)
    if goal:
        authorized_goal(store, c, actor, goal, room)
    # Filter goal-scoped decisions BEFORE LIMIT. Room access alone must not
    # reveal a goal derived from an unreadable source.
    rows = c.execute(
        """SELECT d.*,v.state,v.body,v.issuer,ip.kind AS issuer_kind,v.source,v.event,e.seq AS sequence,
                  e.created_at
           FROM hb_decision d JOIN hb_decision_revision v
             ON v.decision=d.id AND v.revision=d.revision
           JOIN hb_event e ON e.id=v.event
           JOIN hb_principal ip ON ip.id=v.issuer
           LEFT JOIN hb_goal g ON g.id=d.goal
           LEFT JOIN hb_event s ON s.id=g.source
           WHERE d.room=%s AND (%s::uuid IS NULL OR d.id>%s::uuid)
             AND (d.goal IS NULL OR %s OR s.audience='room'
                  OR s.author=%s OR s.recipient=%s)
             AND (%s=false OR d.goal IS NULL OR d.goal=%s::uuid)
           ORDER BY d.id LIMIT %s""",
        (room_id, after, after, actor["administrator"], actor["id"], actor["id"],
         bool(goal) or scope_only, goal, limit + 1),
    ).fetchall()
    return {
        "decisions": rows[:limit],
        "nextCursor": rows[limit - 1]["id"] if len(rows) > limit else None,
    }


def listing(store, actor_id, room, after, limit, goal=None):
    with store.pool.connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        actor = store.actor(c, actor_id)
        value = page(store, c, actor, room, after, limit, goal)
        value["asOfSequence"] = c.execute(
            "SELECT next_seq FROM hb_authority WHERE id=%s", (actor["authority"],)
        ).fetchone()["next_seq"]
        return json_value(value)


def briefing(store, actor_id, delivery_id):
    with store.pool.connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        actor = store.actor(c, actor_id)
        delivery = c.execute(
            "SELECT * FROM hb_delivery WHERE id=%s AND agent=%s",
            (delivery_id, actor["id"]),
        ).fetchone()
        if not delivery or actor["kind"] != "agent":
            raise Denied("Briefing belongs to its execution participant")
        event = store.readable_event(c, actor, delivery["event"])
        room = store.room(c, actor, event["room"])
        goal = event["data"].get("goalId")
        # Work-room permission does not imply parent-goal/source permission.
        visible_goal = None
        if goal:
            row = c.execute(
                """SELECT g.id FROM hb_goal g JOIN hb_event s ON s.id=g.source
                   WHERE g.id=%s AND (%s OR EXISTS(
                     SELECT 1 FROM hb_membership m
                     WHERE m.room=g.room AND m.principal=%s AND m.can_read))
                     AND (%s OR s.audience='room' OR s.author=%s OR s.recipient=%s)""",
                (goal, actor["administrator"], actor["id"], actor["administrator"],
                 actor["id"], actor["id"]),
            ).fetchone()
            visible_goal = row["id"] if row else None
        # Only this room's decisions; parent-room decisions require a separate
        # explicit authorized read, never accidental cross-room propagation.
        local_goal = visible_goal if goal and c.execute(
            "SELECT 1 FROM hb_goal WHERE id=%s AND room=%s",
            (visible_goal, room["id"]),
        ).fetchone() else None
        decisions = page(store, c, actor, str(room["id"]), None, 32,
                         local_goal, scope_only=True)
        # This is a bounded current snapshot, not a claim to cover every scope.
        attempt = c.execute(
            "SELECT * FROM hb_work_attempt WHERE delivery=%s", (delivery_id,)
        ).fetchone()
        head = c.execute(
            "SELECT next_seq FROM hb_authority WHERE id=%s", (actor["authority"],)
        ).fetchone()["next_seq"]
        return json_value({
            "schema": "lax-work-briefing/v1", "authorityId": actor["authority"],
            "agentId": actor["id"], "deliveryId": delivery["id"],
            "generation": delivery["generation"], "claimId": delivery["claim_id"],
            "roomId": room["id"], "roomPurpose": room["purpose"],
            "asOfSequence": head, "deliveryState": delivery["state"],
            "attempt": attempt, **decisions,
            "contentAuthority": "tool-data-with-attributable-issuers",
            "requiresDeeperRead": decisions["nextCursor"] is not None,
        })


def current_sequence(c, room, goal):
    c.execute(
        "SELECT pg_advisory_xact_lock_shared(hashtextextended(%s,0))",
        ("decision-scope:" + str(room),),
    )
    row = c.execute(
        """SELECT COALESCE(max(e.seq),0) AS sequence
           FROM hb_decision d JOIN hb_decision_revision v
             ON v.decision=d.id AND v.revision=d.revision
           JOIN hb_event e ON e.id=v.event
           WHERE d.room=%s AND (d.goal IS NULL OR d.goal=%s)""",
        (room, goal),
    ).fetchone()
    return row["sequence"]


def verify_admission(c, event, capabilities):
    required = set()
    if event["data"].get("attemptId"):
        required.add("work-attempts/v1")
    if c.execute("SELECT 1 FROM hb_decision WHERE room=%s LIMIT 1", (event["room"],)).fetchone():
        required.add("room-briefing/v1")
    if required - set(capabilities):
        raise Conflict("Connector lacks required attempt/briefing capability")
