"""Scoped delivery-failure context, without guessing ambiguous legacy message identities."""

from .recovery import RETIRED_ERROR


def context(
    store,
    *,
    task,
    agent,
    reason,
    phase="unknown",
    error_type=None,
    delivery=None,
    visibility="room",
):
    teams = store.rows(
        "SELECT t.title,t.team,g.name FROM task t JOIN team g ON g.id=t.team WHERE t.id=?", (task,)
    )
    owner = teams[0] if teams else {"title": task, "team": None, "name": None}
    value = {
        "teamId": owner["team"],
        "teamName": owner["name"],
        "task": task,
        "taskTitle": owner["title"],
        "recipient": agent,
        "reason": reason,
        "phase": phase,
        "errorType": error_type,
        "state": "unknown",
        "executionState": "unknown",
        "retryable": False,
        "deliveryId": None,
        "commandId": None,
        "sourceEventId": None,
        "sourceAuthor": None,
        "sourcePreview": None,
    }
    if delivery is None:
        return value
    source = store.get_event(delivery["event"])
    # A public card must never inherit an unrelated private message, even in old data.
    source_team = store.rows("SELECT team FROM task WHERE id=?", (source["task"],))
    if (
        source["visibility"] not in ("room", visibility)
        or not source_team
        or source_team[0]["team"] != owner["team"]
    ):
        return value
    commands = store.rows(
        "SELECT id FROM command WHERE event=? ORDER BY id LIMIT 1", (source["id"],)
    )
    state = delivery["state"]
    known_not_started = phase in {"preparingThread", "legacyBudgetCheck"}
    value.update(
        {
            "deliveryId": delivery["id"],
            "commandId": commands[0]["id"] if commands else None,
            "sourceEventId": source["id"],
            "sourceAuthor": source["author"],
            "sourcePreview": source["body"][:240],
            "state": state,
            "executionState": "confirmed"
            if state == "confirmed"
            else "notStarted"
            if known_not_started and state in {"uncertain", "queued", "superseded"}
            else "unknown",
            "retryable": state in {"uncertain", "queued"}
            and known_not_started
            and phase != "legacyBudgetCheck",
        }
    )
    return value


def summary(value):
    where = f"{value['recipient']} in {value['taskTitle']}"
    state = value["state"]
    if state == "confirmed":
        title = f"Message to {where} delivered after recovery."
    elif state == "superseded":
        title = f"Earlier request to {where} was superseded by verified completion."
    elif state in {"queued", "dispatching"}:
        title = f"Message to {where} is queued or recovering."
    elif value["executionState"] == "notStarted":
        title = f"Message to {where} could not start."
    else:
        title = f"Message to {where} has an unconfirmed delivery."
    preview = f"\nRequest: {value['sourcePreview']}" if value["sourcePreview"] else ""
    advice = (
        "Fix the reported cause before retrying."
        if value["retryable"]
        else "Check delivery status before retrying; the message may already have reached the agent."
    )
    if value["phase"] == "legacyBudgetCheck":
        advice = "This obsolete automatic-turn limit has been removed. Server recovery handles this request; check status before resending."
    if state in {"confirmed", "superseded", "queued", "dispatching"}:
        advice = "No duplicate send is needed."
    return f"{title}{preview}\nReason: {value['reason']}\n{advice}"


def present_event(store, event):
    event = {**event, "data": dict(event["data"])}
    saved = event["data"].get("deliveryFailure")
    prefix = "Could not confirm delivery: "
    legacy = event["kind"] == "attention" and event["body"].startswith(prefix)
    if not saved and not legacy:
        return event
    reason = saved["reason"] if saved else event["body"][len(prefix) :]
    phase = (
        saved.get("phase", "unknown")
        if saved
        else "legacyBudgetCheck"
        if reason == RETIRED_ERROR
        else "unknown"
    )
    delivery = None
    if saved and saved.get("deliveryId") is not None:
        rows = store.rows(
            "SELECT * FROM delivery WHERE id=? AND task=? AND agent=?",
            (saved["deliveryId"], event["task"], event["author"]),
        )
        if rows:
            delivery = rows[0]
    elif legacy:
        rows = store.rows(
            "SELECT * FROM delivery WHERE task=? AND agent=? AND substr(error,1,400)=? AND event<?",
            (event["task"], event["author"], reason, event["id"]),
        )
        if len(rows) == 1:
            delivery = rows[0]
    value = context(
        store,
        task=event["task"],
        agent=event["author"],
        reason=reason,
        phase=phase,
        error_type=saved.get("errorType")
        if saved
        else "LegacyBudgetLimit"
        if phase == "legacyBudgetCheck"
        else None,
        delivery=delivery,
        visibility=event["visibility"],
    )
    event["data"]["deliveryFailure"] = value
    event["body"] = summary(value)
    return event
