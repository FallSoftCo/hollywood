"""Declared room visibility for one native human question response.

The existing response event is shared; no second event, Work or authority ledger
is introduced. Private attention controls remain participant-only.
"""
import json
from uuid import UUID

from .store import Denied


def target(store, c, agent, human, publication, *, prepared=False):
    fields = {"version", "roomId", "roomName"} if prepared else {"version", "roomId"}
    if (not isinstance(publication, dict) or set(publication) != fields
            or type(publication["version"]) is not int or publication["version"] != 1):
        raise ValueError("Exact human response publication version1 required")
    identity = str(UUID(publication["roomId"]))
    if identity != publication["roomId"]:
        raise ValueError("Canonical response room identity required")
    if human["kind"] != "human" or agent["kind"] != "agent":
        raise Denied("Attributable human and original question agent required")
    room = store.room(c, agent, identity)
    store.room(c, agent, identity, "can_post")
    store.room(c, human, identity)
    store.room(c, human, identity, "can_post")
    return room


def prepare(store, c, agent, human, payload):
    publication = payload.get("responsePublication")
    if publication is None:
        return payload["request"], payload["summary"]
    request = payload["request"]
    if (payload["method"] != "hollywood/question"
            or request.get("isBlocking") is not False
            or any(q.get("isSecret", False) is not False for q in request["questions"])):
        raise ValueError("Only nonsecret nonblocking Hollywood questions may declare room responses")
    room = target(store, c, agent, human, publication)
    notice = "Your answer will be shared in room " + json.dumps(room["name"], ensure_ascii=False) + "."
    questions = [{**q, "question": q["question"] + "\n\n" + notice}
                 for q in request["questions"]]
    summary = payload["summary"] + "\n\n" + notice
    if any(len(q["question"]) > 2000 for q in questions) or len(summary) > 6000:
        raise ValueError("Question must leave room for its mandatory response visibility notice")
    prepared = {**request, "questions": questions,
                "responsePublication": {**publication, "roomName": room["name"]}}
    if len(json.dumps({**payload, "request": prepared, "summary": summary}).encode()) > 14000:
        raise ValueError("Question including response visibility exceeds the action limit")
    return prepared, summary


def check(store, c, actor, row):
    publication = row["request"].get("responsePublication")
    if publication is not None:
        if row["method"] != "hollywood/question" or row["request"].get("isBlocking") is not False:
            raise ValueError("Response publication is not a generic approval capability")
        target(store, c, store.actor(c, row["agent"]), actor, publication, prepared=True)
    return publication


def response(store, c, actor, row, answers):
    publication = check(store, c, actor, row)
    if publication is None:
        return None
    body = "\n\n".join(q["header"] + ": " + "\n".join(answers[q["id"]]["answers"])
                       for q in row["request"]["questions"])
    if len(body.encode()) > 6000:
        raise ValueError("Published human answer exceeds the continuation limit")
    return {"room": publication["roomId"], "body": body,
            "data": {"responsePublication": publication, "answers": answers}}
