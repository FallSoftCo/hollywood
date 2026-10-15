"""Per-goal peer cooperation. Room defaults never determine every team's leader.

This first canonical port supports directed allocation and explicit Kanban claims.
Automatic selection, lease handoff and collaborative editing are separate ports.
"""

from .store import Conflict, Denied, json_value

CAPABILITY = "cooperation-plan/v1"


def row(c, goal):
    return c.execute("SELECT * FROM hb_goal_cooperation WHERE goal=%s", (goal,)).fetchone()


def view(c, goal, actor=None):
    policy = row(c, goal)
    if not policy:
        return None
    members = c.execute(
        "SELECT principal FROM hb_cooperation_executor WHERE goal=%s ORDER BY principal",
        (goal,),
    ).fetchall()
    role = None
    if actor is not None:
        role = (
            "verifier"
            if actor == policy["reviewer"]
            else "planner"
            if actor == policy["planner"]
            else "executor"
            if any(m["principal"] == actor for m in members)
            else "observer"
        )
    return json_value(
        {
            "schema": CAPABILITY,
            "policy": policy["policy"],
            "revision": policy["revision"],
            "plannerId": policy["planner"],
            "reviewerId": policy["reviewer"],
            "executorIds": [m["principal"] for m in members],
            "role": role,
            "observationStartsExecution": False,
            "reassignment": "requires-settled-original-attempt",
        }
    )


def configure(store, c, actor, goal, payload):
    old = row(c, goal["id"])
    if (old["revision"] if old else 0) != payload["cooperationRevision"]:
        raise Conflict("Cooperation revision changed")
    if c.execute(
        """SELECT 1 FROM hb_work w WHERE w.goal=%s AND EXISTS(
           SELECT 1 FROM hb_work_attempt a WHERE a.work=w.id) LIMIT 1""", (goal["id"],)
    ).fetchone():
        raise Conflict("Admitted work requires an explicit cooperation migration")
    ids = payload["executorIds"]
    reviewer = payload["reviewerId"]
    if reviewer in ids:
        raise Conflict("Verifier cannot also own implementation in this cooperation contract")
    if str(goal["owner"]) == reviewer:
        raise Conflict("Planner and independent verifier must be distinct")
    for identity in ids + ([reviewer] if reviewer else []):
        member = store.actor(c, identity)
        if member["authority"] != actor["authority"] or member["kind"] != "agent":
            raise Denied("Cooperation member unavailable")
        store.room(c, member, goal["room"], "can_request")
        store.room(c, member, goal["room"], "can_post")
    prior = c.execute("SELECT owner FROM hb_work WHERE goal=%s", (goal["id"],)).fetchall()
    if any(w["owner"] and str(w["owner"]) not in ids for w in prior):
        raise Conflict("Existing planned ownership is outside this executor set")
    if payload["policy"] != "kanban_pull" and any(w["owner"] is None for w in prior):
        raise Conflict("Open work requires Kanban cooperation")
    c.execute(
        """INSERT INTO hb_goal_cooperation(authority,goal,policy,planner,reviewer)
                 VALUES(%s,%s,%s,%s,%s) ON CONFLICT(goal) DO UPDATE
                 SET policy=EXCLUDED.policy,reviewer=EXCLUDED.reviewer,revision=hb_goal_cooperation.revision+1""",
        (actor["authority"], goal["id"], payload["policy"], goal["owner"], reviewer),
    )
    c.execute("DELETE FROM hb_cooperation_executor WHERE goal=%s", (goal["id"],))
    for identity in ids:
        c.execute(
            "INSERT INTO hb_cooperation_executor VALUES(%s,%s,%s)",
            (actor["authority"], goal["id"], identity),
        )


def executor(store, c, goal, identity, room):
    policy = row(c, goal["id"])
    target = store.actor(c, identity)
    if target["authority"] != goal["authority"] or target["kind"] != "agent":
        raise Denied("Execution owner unavailable")
    store.room(c, target, room)
    if (
        policy
        and not c.execute(
            "SELECT 1 FROM hb_cooperation_executor WHERE goal=%s AND principal=%s",
            (goal["id"], identity),
        ).fetchone()
    ):
        raise Denied("Agent is not an executor for this goal")
    return target


def review(store, c, actor, goal):
    policy = row(c, goal["id"])
    if policy and policy["reviewer"]:
        if not actor["administrator"] and actor["id"] != policy["reviewer"]:
            raise Denied("Designated independent verifier required")
        store.room(c, actor, goal["room"], "can_post")
    else:
        from .work import manage

        manage(actor, goal)


def verify_admission(store, c, actor, event, capabilities):
    goal = event["data"].get("goalId")
    policy = row(c, goal) if goal else None
    if not policy:
        return
    parent = c.execute("SELECT room FROM hb_goal WHERE id=%s", (goal,)).fetchone()
    store.room(c, actor, parent["room"])
    if event["data"].get("workId"):
        executor(
            store, c, {"id": goal, "authority": actor["authority"]}, actor["id"], event["room"]
        )
    if event["data"].get("goalReview") and actor["id"] != (policy["reviewer"] or policy["planner"]):
        raise Denied("Review ownership changed")
    if CAPABILITY not in capabilities:
        raise Conflict("Cooperation-aware connector required")
    if event["data"].get("cooperationRevision") != policy["revision"]:
        raise Conflict("Captured cooperation requires explicit reconciliation")


def reviewer_binding(c, actor, goal, event):
    policy = row(c, goal)
    return bool(
        policy
        and policy["reviewer"] == actor["id"]
        and event["data"].get("goalReview")
        and event["recipient"] == actor["id"]
    )
