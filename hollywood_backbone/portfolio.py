"""Coalesced portfolio obligations for one installed, fully empowered Global.

Committed typed state changes feed a deterministic reconciler. Passive chat,
execution logs, Global's own reports and repeated unchanged observations never
supply model work. A review is private to Global; source audiences do not expand.
"""

from psycopg.types.json import Jsonb

from .store import Conflict, Denied, digest, json_value, uid


def configure(store, actor_id, agent_id, operation, payload):
    with store.pool.connection() as c:
        actor = store.actor(c, actor_id)
        store.admin(actor)
        agent = store.actor(c, agent_id)
        role = c.execute("SELECT * FROM hb_agent_role WHERE principal=%s", (agent_id,)).fetchone()
        if (
            agent["authority"] != actor["authority"]
            or not agent["administrator"]
            or agent["kind"] != "agent"
            or not role
            or role["primary_role"] != "global"
        ):
            raise Denied("Installed Global administrator required")
        room = store.room(c, agent, payload["roomId"], "can_request")
        if role["home_room"] != room["id"]:
            raise Conflict("Portfolio supervision belongs to Global's home")
        c.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            ("portfolio-policy:" + agent_id,),
        )
        fingerprint = digest({"agent": agent_id, **payload})
        old = c.execute(
            "SELECT * FROM hb_portfolio_command WHERE principal=%s AND operation=%s",
            (actor_id, operation),
        ).fetchone()
        if old:
            if old["digest"] != fingerprint:
                raise Conflict("Portfolio policy operation changed")
            return old["result"]
        prior = c.execute(
            "SELECT * FROM hb_portfolio_policy WHERE principal=%s FOR UPDATE", (agent_id,)
        ).fetchone()
        if (prior["revision"] if prior else 0) != payload["revision"]:
            raise Conflict("Portfolio policy revision changed")
        head = c.execute(
            "SELECT next_seq FROM hb_authority WHERE id=%s", (actor["authority"],)
        ).fetchone()["next_seq"]
        activation = (
            head
            if not prior or not prior["enabled"] and payload["enabled"]
            else prior["activation_seq"]
        )
        c.execute(
            """INSERT INTO hb_portfolio_policy
              (principal,authority,room,enabled,activation_seq,scanned_seq,reviews_per_window,window_seconds)
              VALUES(%s,%s,%s,%s,%s,%s,%s,%s)
              ON CONFLICT(principal) DO UPDATE SET enabled=EXCLUDED.enabled,
               activation_seq=EXCLUDED.activation_seq,room=EXCLUDED.room,
               reviews_per_window=EXCLUDED.reviews_per_window,window_seconds=EXCLUDED.window_seconds,
               revision=hb_portfolio_policy.revision+1""",
            (
                agent_id,
                actor["authority"],
                room["id"],
                payload["enabled"],
                activation,
                head,
                payload["reviewsPerWindow"],
                payload["windowSeconds"],
            ),
        )
        revision = c.execute(
            "SELECT revision FROM hb_portfolio_policy WHERE principal=%s", (agent_id,)
        ).fetchone()["revision"]
        result = json_value(
            {
                "accepted": True,
                "agentId": agent_id,
                "revision": revision,
                "activationSequence": activation,
                "historicalWorkResumed": False,
            }
        )
        c.execute(
            "INSERT INTO hb_portfolio_command VALUES(%s,%s,%s,%s)",
            (actor_id, operation, fingerprint, Jsonb(result)),
        )
        return result


def material(c, actor, policy):
    head = c.execute(
        "SELECT next_seq FROM hb_authority WHERE id=%s", (actor["authority"],)
    ).fetchone()["next_seq"]
    rows = c.execute(
        """SELECT * FROM hb_event WHERE authority=%s AND seq>%s AND seq<=%s AND author<>%s
           AND COALESCE(data->>'replyRecipientId','')<>%s
           AND (data->>'workflowAction' IN ('work.report','work.review','work.cancel','goal.state')
             OR data->>'type' IN ('decisionState','executionState')
             OR (kind='result' AND data->>'deliveryId' IS NOT NULL))
           ORDER BY seq LIMIT 256""",
        (
            actor["authority"],
            max(policy["scanned_seq"], policy["activation_seq"]),
            head,
            actor["id"],
            str(actor["id"]),
        ),
    ).fetchall()
    for event in rows:
        goals = [event["data"]["goalId"]] if event["data"].get("goalId") else []
        if event["data"].get("type") == "decisionState" and not goals:
            goals = [
                r["id"]
                for r in c.execute(
                    "SELECT id FROM hb_goal WHERE room=%s", (event["room"],)
                ).fetchall()
            ]
        scopes = [("goal", g) for g in goals]
        if not scopes and event["data"].get("deliveryId"):
            incident = c.execute(
                "SELECT id,state FROM hb_delivery WHERE id=%s AND authority=%s",
                (event["data"]["deliveryId"], actor["authority"]),
            ).fetchone()
            if incident and (
                incident["state"] in ("failed", "unknown")
                or c.execute(
                    """SELECT 1 FROM hb_portfolio_item WHERE principal=%s
                       AND scope='delivery' AND reference=%s""",
                    (actor["id"], incident["id"]),
                ).fetchone()
            ):
                scopes = [("delivery", incident["id"])]
        for scope, reference in scopes:
            if (
                scope == "goal"
                and not c.execute(
                    "SELECT 1 FROM hb_goal WHERE id=%s AND authority=%s",
                    (reference, actor["authority"]),
                ).fetchone()
            ):
                continue
            c.execute(
                """INSERT INTO hb_portfolio_item
                   (principal,scope,reference,goal,delivery,source_event,latest_seq)
                   VALUES(%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT(principal,scope,reference) DO UPDATE SET
                   source_event=EXCLUDED.source_event,latest_seq=EXCLUDED.latest_seq""",
                (
                    actor["id"],
                    scope,
                    reference,
                    reference if scope == "goal" else None,
                    reference if scope == "delivery" else None,
                    event["id"],
                    event["seq"],
                ),
            )
    cursor = rows[-1]["seq"] if len(rows) == 256 else head
    c.execute(
        "UPDATE hb_portfolio_policy SET scanned_seq=%s WHERE principal=%s", (cursor, actor["id"])
    )


def dispatch(store, actor_id, host, capabilities):
    with store.pool.connection() as c:
        actor = store.actor(c, actor_id)
        if actor["kind"] != "agent":
            raise Denied("Execution participant required")
        policy = c.execute(
            "SELECT * FROM hb_portfolio_policy WHERE principal=%s FOR UPDATE", (actor_id,)
        ).fetchone()
        if not policy or not policy["enabled"]:
            return {"admitted": [], "modelsStarted": 0, "reason": "supervision_disabled"}
        if (
            not actor["administrator"]
            or not c.execute(
                "SELECT 1 FROM hb_placement WHERE agent=%s AND host=%s AND enabled",
                (actor_id, host),
            ).fetchone()
        ):
            raise Denied("Installed enrolled Global required")
        if "portfolio-supervision/v1" not in capabilities:
            raise Conflict("Portfolio-aware connector required")
        store.native_role_capability(c, actor, capabilities)
        material(c, actor, policy)
        # One pending or active review across all goals. New material is retained
        # while Global is busy; no duplicate context or model admission is made.
        if c.execute(
            """SELECT 1 FROM hb_delivery d JOIN hb_event e ON e.id=d.event
               WHERE d.agent=%s AND e.data->>'portfolioReview'='true'
               AND d.state IN ('pending','claimed','running','unknown','cancel_requested') LIMIT 1""",
            (actor_id,),
        ).fetchone():
            return {"admitted": [], "modelsStarted": 0, "reason": "original_review_unsettled"}
        c.execute(
            """UPDATE hb_portfolio_policy SET window_started=clock_timestamp(),reviews_used=0
               WHERE principal=%s AND window_started+window_seconds*interval '1 second'<=clock_timestamp()""",
            (actor_id,),
        )
        policy = c.execute(
            "SELECT * FROM hb_portfolio_policy WHERE principal=%s", (actor_id,)
        ).fetchone()
        if policy["reviews_used"] >= policy["reviews_per_window"]:
            return {"admitted": [], "modelsStarted": 0, "reason": "supervision_budget_held"}
        items = c.execute(
            """SELECT i.* FROM hb_portfolio_item i
               LEFT JOIN hb_goal g ON g.id=i.goal
               LEFT JOIN hb_goal_cooperation team ON team.goal=g.id
               LEFT JOIN hb_delivery incident ON incident.id=i.delivery
               WHERE i.principal=%s AND i.latest_seq>i.dispatched_seq AND i.latest_seq>%s
               AND ((i.scope='delivery' AND incident.state IN ('failed','unknown','completed','cancelled'))
                 OR (i.scope='goal' AND (
                   g.state IN ('accepted','cancelled','cancel_requested')
                   OR EXISTS(SELECT 1 FROM hb_work w LEFT JOIN hb_delivery d ON d.id=w.delivery
                     WHERE w.goal=g.id AND (w.state IN ('blocked','rejected')
                       OR d.state IN ('failed','unknown','cancelled')
                       OR (COALESCE(team.reviewer,g.owner)=%s AND w.state='submitted' AND d.state='completed')))
                   OR (g.state='active' AND EXISTS(SELECT 1 FROM hb_work w WHERE w.goal=g.id)
                     AND NOT EXISTS(SELECT 1 FROM hb_work w WHERE w.goal=g.id AND w.state<>'accepted')
                     AND NOT EXISTS(SELECT 1 FROM hb_delivery d WHERE d.event=g.review_request
                       AND d.state IN ('pending','claimed','running','unknown','cancel_requested')))
                 )))
               ORDER BY i.latest_seq LIMIT 64""",
            (actor_id, policy["activation_seq"], actor_id),
        ).fetchall()
        for item in items:
            if item["scope"] == "goal":
                goal = c.execute(
                    "SELECT * FROM hb_goal WHERE id=%s", (item["reference"],)
                ).fetchone()
                works = c.execute(
                    """SELECT w.id,w.state,w.specification,w.acceptance,w.evidence,
                      CASE WHEN w.state IN ('blocked','rejected') THEN w.note ELSE '' END AS blocker,
                      d.state AS execution_state,w.room
                      FROM hb_work w LEFT JOIN hb_delivery d ON d.id=w.delivery
                      WHERE w.goal=%s ORDER BY w.id""",
                    (item["reference"],),
                ).fetchall()
                team = c.execute(
                    "SELECT reviewer FROM hb_goal_cooperation WHERE goal=%s", (goal["id"],)
                ).fetchone()
                reviewer = team["reviewer"] if team and team["reviewer"] else goal["owner"]
                ready = goal["state"] in ("accepted", "cancelled", "cancel_requested") or any(
                    w["state"] in ("blocked", "rejected")
                    or w["execution_state"] in ("failed", "unknown", "cancelled")
                    or reviewer == actor["id"]
                    and w["state"] == "submitted"
                    and w["execution_state"] == "completed"
                    for w in works
                )
                decisions = c.execute(
                    """SELECT id,revision FROM hb_decision WHERE
                       room=ANY(%s::uuid[]) AND (goal IS NULL OR goal=%s) ORDER BY id""",
                    (list({goal["room"], *(w["room"] for w in works)}), goal["id"]),
                ).fetchall()
                snapshot = {"state": goal["state"], "work": works, "decisionRevisions": decisions}
            else:
                incident = c.execute(
                    """SELECT id,state,agent,host,generation,native_thread,native_turn,
                       cancellation_requested FROM hb_delivery WHERE id=%s""",
                    (item["reference"],),
                ).fetchone()
                ready = incident["state"] in ("failed", "unknown", "completed", "cancelled")
                snapshot = {"executionIncident": incident}
            if not ready and item["scope"] == "goal":
                # SQL eligibility also admits an active all-accepted plan once
                # its designated reviewer is no longer executing.
                ready = bool(works) and all(w["state"] == "accepted" for w in works)
            if not ready:
                continue
            key = digest(json_value(snapshot))
            if key == item["dispatched_digest"]:
                c.execute(
                    "UPDATE hb_portfolio_item SET dispatched_seq=latest_seq WHERE principal=%s AND scope=%s AND reference=%s",
                    (actor_id, item["scope"], item["reference"]),
                )
                continue
            source = store.readable_event(c, actor, item["source_event"])
            store.room(c, actor, policy["room"], "can_request")
            event = store.event(
                c,
                actor,
                policy["room"],
                "request",
                "Review the current portfolio obligation for "
                + item["scope"]
                + " "
                + str(item["reference"])
                + ". Read the current goal/plan and sourced decisions, or hollywood_execution_read for an incident. Inspect original receipts and actual evidence where needed, "
                "and take only its authorized next action or report its material result/blocker. "
                "Unknown execution is not permission to retry. Publish an attributable concise result. "
                "This is one bounded review; do not poll, self-request or start unrelated work.",
                audience="direct",
                recipient=actor_id,
                source=source["id"],
                data={
                    "portfolioReview": True,
                    "goalReferenceId": str(item["reference"]) if item["scope"] == "goal" else None,
                    "deliveryReferenceId": str(item["reference"])
                    if item["scope"] == "delivery"
                    else None,
                    "materialSequence": item["latest_seq"],
                    "triggerDigest": key,
                },
            )
            delivery = uid()
            c.execute(
                "INSERT INTO hb_delivery(id,authority,event,agent) VALUES(%s,%s,%s,%s)",
                (delivery, actor["authority"], event, actor_id),
            )
            c.execute(
                """UPDATE hb_portfolio_item SET dispatched_seq=latest_seq,
                   dispatched_digest=%s,request_event=%s WHERE principal=%s AND scope=%s AND reference=%s""",
                (key, event, actor_id, item["scope"], item["reference"]),
            )
            c.execute(
                "UPDATE hb_portfolio_policy SET reviews_used=reviews_used+1 WHERE principal=%s",
                (actor_id,),
            )
            return json_value(
                {
                    "admitted": [
                        {
                            "scope": item["scope"],
                            "reference": item["reference"],
                            "requestEventId": event,
                            "deliveryId": delivery,
                        }
                    ],
                    "modelsStarted": 0,
                }
            )
        return {"admitted": [], "modelsStarted": 0, "reason": "no_new_actionable_material"}


def read(store, actor_id, agent_id, after=None, limit=100):
    with store.pool.connection() as c:
        actor = store.actor(c, actor_id)
        target = store.actor(c, agent_id)
        if target["authority"] != actor["authority"] or (
            not actor["administrator"] and str(actor["id"]) != agent_id
        ):
            raise Denied("Portfolio state unavailable")
        policy = c.execute(
            "SELECT * FROM hb_portfolio_policy WHERE principal=%s", (agent_id,)
        ).fetchone()
        items = c.execute(
            """SELECT i.*,d.state AS review_execution_state,d.id AS review_delivery,
               d.native_thread,d.native_turn
               FROM hb_portfolio_item i LEFT JOIN hb_delivery d ON d.event=i.request_event
               WHERE i.principal=%s AND (%s::uuid IS NULL
                 OR (i.reference,i.scope)>(%s::uuid,%s))
               ORDER BY i.reference,i.scope LIMIT %s""",
            (
                agent_id,
                after["reference"] if after else None,
                after["reference"] if after else None,
                after["scope"] if after else None,
                limit + 1,
            ),
        ).fetchall()
        return json_value(
            {
                "policy": policy,
                "items": items[:limit],
                "nextCursor": {
                    "reference": items[limit - 1]["reference"],
                    "scope": items[limit - 1]["scope"],
                }
                if len(items) > limit
                else None,
                "meaning": "Dispatched is not reviewed or accepted. Unknown/stopped reviews retain their original receipt; no retry is inferred.",
            }
        )
