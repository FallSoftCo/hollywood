"""Peer requests and result handoffs for one agent's purpose contexts.

Communication acceptance uses Store.post_in_transaction. Material is only the
internal handoff; canonical requests, deliveries and results remain Hollywood's.
"""
import json
from uuid import UUID, uuid5
from psycopg.types.json import Jsonb
from .store import Conflict, Denied, json_value
from .context_system import item, change


def directory(c, store, actor, value):
    room = store.room(c, actor, str(UUID(value["roomId"])))
    from .context_material import wire
    after = str(UUID(value["after"])) if value.get("after") else None
    rows = store.room_actors(c, actor, room, after=after, limit=21)
    result = {"roomId":str(room["id"]), "name":room["name"],
              "actors":[], "nextCursor":None, "meaning":"Canonical roster with bounded responsibility previews."}
    for row in rows:
        entry={k:json_value(row[k]) for k in ("id","name","kind","primary_role","home_room")}
        entry["purposePreview"]=(row["purpose"] or "")[:256]
        result["actors"].append(entry)
        if len(wire(result))>5800:
            result["actors"].pop()
            break
    if result["actors"] and (len(result["actors"])<len(rows) or len(rows)==21):
        result["nextCursor"]=result["actors"][-1]["id"]
    return result


def require_current_read(binding, row, turn):
    if binding["reads"].get(str(row["id"])) != {"revision": row["revision"], "turn": turn}:
        raise Conflict("Read current complete work material before contributing")


def own_change(c, actor, binding, row, state, value, turn):
    change(c, row, state, value)
    reads = dict(binding["reads"])
    reads[str(row["id"])] = {"revision": row["revision"] + 1, "turn": turn}
    c.execute("UPDATE hb_purpose_context SET reads=%s WHERE agent=%s AND purpose='writer'",
              (Jsonb(reads), actor["id"]))


def requests(c, actor, work):
    return c.execute("""SELECT * FROM hb_context_material WHERE agent=%s AND kind='need'
      AND value->>'parentWorkId'=%s ORDER BY created_at,id""",
                     (actor["id"], str(work["id"]))).fetchall()


def queue(c, store, actor, binding, p):
    v = p["value"]
    work = item(c, store, actor, v["workId"], "work")
    require_current_read(binding, work, p["nativeTurn"])
    delivery=c.execute("SELECT cancellation_requested FROM hb_delivery WHERE event=%s AND agent=%s",
                       (work["source"],actor["id"])).fetchone()
    if work["state"] not in ("running", "pending") or delivery["cancellation_requested"]:
        raise Conflict("Work no longer admits a peer request")
    original = store.readable_event(c, actor, work["source"])
    room_id = str(UUID(v.get("roomId") or str(original["room"])))
    recipient = str(UUID(v["recipientId"]))
    store.room(c, actor, room_id, "can_request")
    peer = c.execute("""SELECT * FROM hb_principal WHERE id=%s AND authority=%s
      AND active AND kind='agent'""", (recipient, actor["authority"])).fetchone()
    if not peer or recipient == str(actor["id"]):
        raise Denied("A distinct active peer in this authority is required")
    store.room(c, peer, room_id)
    # A direct source is not permission to disclose it to a different peer.
    if original["audience"] == "direct":
        raise Denied("Private delegation requires an explicit sharing contract")
    body = v["body"]
    if not isinstance(body, str) or not body.strip() or len(body.encode()) > 6000:
        raise ValueError("A bounded peer specification is required")
    evidence = v.get("evidenceReferences")
    if evidence is not None:
        from .execution_evidence import snapshot
        snapshot(c, store, actor, evidence)  # Validate now; recheck before publication.
    need_id = str(uuid5(UUID(str(work["id"])), p["operationId"]))
    value = {"kind": "request", "required": True, "parentWorkId": str(work["id"]),
             "material": body, "prerequisites": [], "target": {
                 "roomId": room_id, "recipientId": recipient, "audience": original["audience"]}}
    if evidence is not None:
        value["evidenceReferences"] = evidence
    c.execute("""INSERT INTO hb_context_material(id,agent,kind,source,state,value)
      VALUES(%s,%s,'need',%s,'ready',%s)""",
              (need_id, actor["id"], work["source"], Jsonb(value)))
    peers = work["value"].get("peerRequests", []) + [{"communicationId": need_id,
             "recipientId": recipient, "roomId": room_id, "state": "queued"}]
    own_change(c, actor, binding, work, work["state"],
               {**work["value"], "peerRequests": peers}, p["nativeTurn"])
    return {"communicationId": need_id, "state": "queued",
            "meaning": "Durable communication intent; not yet an accepted peer request."}


def wait(c, store, actor, binding, p):
    work = item(c, store, actor, p["value"]["workId"], "work")
    require_current_read(binding, work, p["nativeTurn"])
    if work["state"] not in ("running", "pending"):
        raise Conflict("Work no longer admits waiting")
    needs = requests(c, actor, work)
    waiting = [str(n["id"]) for n in needs if not n["value"].get("peerResult")]
    if not waiting:
        return {"workId": str(work["id"]), "state": "results_available",
                "instruction": "Read current work and continue; no outstanding peer prerequisite exists."}
    own_change(c, actor, binding, work, "waiting",
               {**work["value"], "waitingOn": waiting}, p["nativeTurn"])
    return {"workId": str(work["id"]), "state": "waiting", "prerequisites": waiting,
            "instruction": "End this inference. Actual peer results will reach the reader; original work remains open."}


def publish(c, store, actor, need, provenance, supplied_body):
    value = need["value"]
    if supplied_body is not None and supplied_body != value["material"]:
        raise Conflict("Peer specification is immutable; omit body to send the recorded material")
    target = value["target"]
    operation = "context-peer:" + str(need["id"])
    payload = {"kind": "request", "body": value["material"], "expectsReply": True,
               "recipientId": target["recipientId"], "audience": target["audience"],
               "sourceEventId": str(need["source"])}
    if value.get("evidenceReferences") is not None:
        payload["evidenceReferences"] = value["evidenceReferences"]
    wire = {"method": "POST", "path": "rooms/" + target["roomId"] + "/messages",
            "body": {"operationId": operation, **payload}}
    result = store.post_in_transaction(c, actor, target["roomId"], operation, payload,
                                      wire=wire, native_context=provenance)
    delivery = result["deliveries"][0]["id"]
    event = result["event"]["id"]
    change(c, need, "published", {**value, "publishedEventId": event,
                                "requestEventId": event, "deliveryId": delivery})
    work = item(c, store, actor, value["parentWorkId"], "work")
    peers = [{**x, "state": "accepted", "requestEventId": event, "deliveryId": delivery}
             if x["communicationId"] == str(need["id"]) else x
             for x in work["value"]["peerRequests"]]
    change(c, work, work["state"], {**work["value"], "peerRequests": peers})
    return result


def incoming(c, agent, event):
    row = c.execute("SELECT data FROM hb_event WHERE id=%s", (event,)).fetchone()
    if row["data"].get("type") != "peerCompletion":
        from .context_peer_updates import relation
        return relation(c,agent,event)
    need = c.execute("""SELECT id,value FROM hb_context_material WHERE agent=%s
      AND kind='need' AND value->>'requestEventId'=%s""",
                     (agent, row["data"]["replyToRequest"])).fetchone()
    return {"responseRequired": False, "peerCompletion": True,
            **({"parentWorkId": need["value"]["parentWorkId"],
                "communicationId": str(need["id"])} if need else {})}


def apply(c, store, actor, binding, p):
    source = item(c, store, actor, p["value"]["inputId"], "input")
    if source["state"] != "pending" or not source["value"].get("peerCompletion"):
        raise Conflict("Original pending peer completion required")
    event = store.readable_event(c, actor, source["source"])
    value = source["value"]
    outcome = "unmapped_result_recorded"
    if value.get("parentWorkId"):
        need = item(c, store, actor, value["communicationId"], "need")
        child = c.execute("SELECT * FROM hb_delivery WHERE id=%s", (need["value"]["deliveryId"],)).fetchone()
        request = store.readable_event(c, actor, need["value"]["requestEventId"])
        result = store.readable_event(c, actor, event["source"])
        if (event["data"].get("replyToRequest") != str(request["id"])
            or event["data"].get("replyToDelivery") != str(child["id"])
            or child["result_event"] != result["id"] or child["agent"] != event["author"]
            or request["recipient"] != event["author"]
            or child["state"] not in ("completed", "failed", "cancelled")):
            raise Conflict("Canonical peer result differs from the original delegation")
        work = item(c, store, actor, value["parentWorkId"], "work")
        peer_result = {"requestEventId": str(request["id"]), "deliveryId": str(child["id"]),
                       "resultEventId": str(result["id"]), "executionState": child["state"],
                       "interpretation": p["value"]["body"]}
        change(c, need, need["state"], {**need["value"], "peerResult": peer_result})
        peers = [{**x, "state": "result_recorded", "peerResult": peer_result}
                 if x["communicationId"] == str(need["id"]) else x
                 for x in work["value"]["peerRequests"]]
        remaining = [n for n in requests(c, actor, work)
                     if str(n["id"]) != str(need["id"]) and not n["value"].get("peerResult")]
        parent = c.execute("SELECT cancellation_requested FROM hb_delivery WHERE event=%s AND agent=%s",
                           (work["source"], actor["id"])).fetchone()
        open_work = work["state"] not in ("complete","cancelled") and not parent["cancellation_requested"]
        resume = work["state"] == "waiting" and not remaining and open_work
        state = "pending" if resume else work["state"]
        change(c, work, state, {**work["value"], "peerRequests": peers,
               "resumeRevision": work["value"].get("resumeRevision", 0) + int(open_work)})
        outcome = "original_work_ready" if resume else "result_recorded"
    change(c, source, "handled", {**value, "interpretation": p["value"]["body"]})
    c.execute("""UPDATE hb_delivery SET state='completed',result_event=%s,revision=revision+1,
      updated_at=clock_timestamp() WHERE event=%s AND agent=%s AND state='pending'""",
              (event["source"], event["id"], actor["id"]))
    return {"inputId": str(source["id"]), "state": outcome,
            "responseRequired": False, "meaning": "Peer execution result recorded; work acceptance is still the writer/reviewer's responsibility."}
