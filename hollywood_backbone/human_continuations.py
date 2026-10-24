"""Human questions survive input handling; answers enter the same agent context.

This extends the existing Attention and delivery records. It starts no runtime
and never represents an accepted human reply as an agent or developer message.
"""

import json

from .store import Conflict, Denied, uid

QUESTION_METHODS = ("hollywood/question", "item/tool/requestUserInput")


def nonblocking(row):
    return row["method"] in QUESTION_METHODS and row["request"].get("isBlocking") is False


def context(c, row):
    system=c.execute("SELECT * FROM hb_context_system WHERE agent=%s AND enabled",
                     (row["agent"],)).fetchone()
    if system:
        binding=c.execute("SELECT * FROM hb_purpose_context WHERE agent=%s AND purpose='writer'",
                          (row["agent"],)).fetchone()
        begin=c.execute("""SELECT result FROM hb_context_operation WHERE agent=%s AND purpose='writer'
            AND operation=%s""",(row["agent"],"context-begin:"+row["native_turn"])).fetchone()
        matches=[s for s in begin["result"].get("executionScopes",[]) if
            s.get("deliveryId")==str(row["delivery"]) and s.get("generation")==row["generation"]
            and s.get("nativeThread")==row["native_thread"] and s.get("nativeTurn")==row["native_turn"]] if begin else []
        if (not binding or len(matches)!=1 or binding["native_thread"]!=row["native_thread"]
                or str(binding["context_id"])!=matches[0]["contextId"]):
            raise Conflict("Original purpose question context changed")
        return {"principal":row["agent"],"context_id":str(binding["context_id"]),
                "native_thread":binding["native_thread"],"host":system["host"],
                "workspace_id":system["workspace"],"bindingKind":"purpose"}
    original = c.execute(
        "SELECT * FROM hb_agent_execution WHERE delivery=%s AND generation=%s",
        (row["delivery"], row["generation"]),
    ).fetchone()
    current = c.execute(
        "SELECT * FROM hb_agent_context WHERE principal=%s", (row["agent"],)
    ).fetchone()
    if (not original or not current
            or original["principal"] != row["agent"]
            or original["native_thread"] != row["native_thread"]
            or current["native_thread"] != row["native_thread"]
            or current["context_id"] != original["context_id"]):
        raise Conflict("Original question context changed; explicit reconciliation required")
    return current


def accepting(c, row, delivery):
    if delivery["cancellation_requested"]:
        return False
    if delivery["state"] in ("claimed", "running") or (
            nonblocking(row) and delivery["state"] == "completed"):
        if nonblocking(row):
            context(c, row)
        return True
    return False


def can_respond(store, c, actor, row, delivery):
    question = row["method"] in QUESTION_METHODS
    authorized = (actor["id"] == row["human"] or actor["administrator"])
    if question:
        authorized = authorized and actor["kind"] == "human"
    try:
        if not (authorized and row["state"] == "pending" and accepting(c, row, delivery)):
            return False
        if nonblocking(row):
            store.room(c, actor, row["room"], "can_request")
            target = store.actor(c, row["agent"])
            if target["kind"] != "agent":
                return False
            store.room(c, target, row["room"])
            from .human_decision_publication import check
            check(store, c, actor, row)
        return True
    except (Conflict, Denied):
        return False


def enqueue(store, c, actor, row, delivery, response):
    """Commit the authenticated answer and exactly one canonical queued input."""
    if not nonblocking(row):
        raise ValueError("Only a nonblocking question creates a continuation")
    binding = context(c, row)
    store.room(c, actor, row["room"], "can_request")
    target = store.actor(c, row["agent"])
    if target["kind"] != "agent":
        raise Denied("Original question agent unavailable")
    store.room(c, target, row["room"])
    body = json.dumps({
        "type": "hollywoodQuestionAnswer", "attentionId": str(row["id"]),
        "answers": response["answers"],
    })
    if len(body.encode()) > 6000:
        raise ValueError("Question answer exceeds the 6000-byte continuation limit")
    from .human_decision_publication import response as publish_response
    publication = publish_response(store, c, actor, row, response["answers"])
    # One canonical answer keeps its original continuation even when its
    # declared audience is a different room; agent contexts are not room-bound.
    event = store.event(
        c, actor, publication["room"] if publication else row["room"],
        "message" if publication else "request", publication["body"] if publication else body,
        recipient=row["agent"], audience="room" if publication else "direct",
        source=row["event"],
        data={"type": "humanQuestionAnswer", "attentionId": str(row["id"]),
              "originalDeliveryId": str(delivery["id"]),
              "contextId": binding["context_id"],
              **(publication["data"] if publication else {})},
    )
    c.execute(
        "INSERT INTO hb_delivery(id,authority,event,agent) VALUES(%s,%s,%s,%s)",
        (uid(), actor["authority"], event, row["agent"]),
    )
    from .context_system import ingest
    ingest(c,row["agent"],event)
    return event


def continuation(c, row):
    if not nonblocking(row) or not row["response_event"]:
        return None
    value = c.execute(
        "SELECT id,event,state,native_thread,native_turn FROM hb_delivery WHERE event=%s",
        (row["response_event"],),
    ).fetchone()
    return dict(value) if value else None


def settle(store, c, actor, delivery, state):
    """Record reply input admission separately from the parent task's completion."""
    rows = c.execute(
        """SELECT * FROM hb_attention WHERE response_event=%s
           AND state IN ('response_submitted','native_submitted') FOR UPDATE""",
        (delivery["event"],),
    ).fetchall()
    for row in rows:
        if not nonblocking(row):
            continue
        if state == "running":
            if delivery["native_thread"] != row["native_thread"]:
                raise Conflict("Question answer admitted to another native context")
            after = "native_submitted"
        elif state == "completed":
            after = "closed"
        elif state == "cancelled":
            after = "expired"
        elif state in ("failed", "unknown"):
            after = "unknown"
        else:
            continue
        if after == row["state"]:
            continue
        c.execute(
            """UPDATE hb_attention SET state=%s,revision=revision+1,
               updated_at=clock_timestamp() WHERE id=%s""", (after, row["id"]),
        )
        store.event(
            c, actor, row["room"], "control", "Question answer input " + after,
            recipient=row["human"], audience="direct", source=row["event"],
            data={"attentionId": str(row["id"]), "state": after,
                  "continuationDeliveryId": str(delivery["id"]),
                  "semanticWorkCompletionProved": False},
        )
