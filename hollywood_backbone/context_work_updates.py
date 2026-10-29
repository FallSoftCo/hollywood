"""Reader contributions amend accepted work without steering a running writer."""
from uuid import UUID
from .store import Conflict,Denied,json_value
from .context_system import item,change,need_for
from .context_peers import require_current_read


def apply(c,store,actor,binding,p):
    v=p["value"]
    if set(v)!={"inputId","workId","workRevision","body"}:
        raise ValueError("Exact interpreted input and original work revision required")
    source=item(c,store,actor,v["inputId"],"input")
    if source["value"].get("writerOutcome"):
        raise Conflict("Native outcome requires original-work review; causal human authorship must not be reused")
    from .context_assignments import require_separate
    require_separate(c,actor,source)
    work=item(c,store,actor,v["workId"],"work")
    require_current_read(binding,source,p["nativeTurn"])
    require_current_read(binding,work,p["nativeTurn"])
    if (source["state"]!="pending" or work["state"] not in ("pending","running","waiting")
            or work["revision"]!=v["workRevision"]):
        raise Conflict("Original input or accepted work changed")
    incoming=store.readable_event(c,actor,source["source"])
    original=store.readable_event(c,actor,work["source"])
    d=c.execute("SELECT * FROM hb_delivery WHERE event=%s AND agent=%s",
                (original["id"],actor["id"])).fetchone()
    answer=c.execute("SELECT * FROM hb_delivery WHERE event=%s AND agent=%s",
                     (incoming["id"],actor["id"])).fetchone()
    if not d or not answer or d["cancellation_requested"] or answer["cancellation_requested"]:
        raise Conflict("Original communication was cancelled")
    if source["value"].get("peerCompletion"):
        raise ValueError("Peer completion uses its original peer-result contract")
    question=incoming["data"].get("type")=="humanQuestionAnswer"
    if question:
        if incoming["data"].get("originalDeliveryId")!=str(d["id"]):
            raise Denied("Question answer belongs to different original work")
    elif source["value"].get("peerContinuation"):
        from .context_peer_updates import relation
        causal=relation(c,actor["id"],source["source"])
        if not causal or causal["parentWorkId"]!=str(work["id"]):
            raise Denied("Peer follow-up has no current original delegation")
    elif (incoming["room"]!=original["room"] or incoming["audience"]!=original["audience"]
          or incoming["audience"]=="direct" and incoming["author"]!=original["author"]):
        raise Denied("Work correction requires the original communication scope")
    body=v["body"]
    if not isinstance(body,str) or not body.strip() or len(body)>4000:
        raise ValueError("Bounded interpretation required")
    entry={"inputId":str(source["id"]),"sourceEventId":str(incoming["id"]),
        "authorId":str(incoming["author"]),"authorKind":incoming["data"].get("authorKind"),
        "roomId":str(incoming["room"]),"audience":incoming["audience"],"interpretation":body}
    updates=(work["value"].get("updates",[])+[entry])[-16:]
    # The original objective/acceptance and canonical input remain intact. A
    # version change is visible at the writer's next read, never a turn/steer.
    if source["value"].get("unadmittedWorkId"):
        child=item(c,store,actor,source["value"]["unadmittedWorkId"],"work")
        if (child["source"]!=source["source"] or child["state"]!="pending"
                or answer["state"]!="pending" or answer["generation"]!=0
                or child["value"].get("execution") or child["value"].get("peerRequests")):
            raise Conflict("Previously admitted work cannot be merged by interpretation")
        change(c,child,"superseded",{**child["value"],"supersededByWorkId":str(work["id"]),
            "reason":"Causally linked peer continuation; no execution was admitted"})
    change(c,source,"handled",{**source["value"],"appliedToWorkId":str(work["id"])})
    if question:
        completed=c.execute("""UPDATE hb_delivery SET state='completed',result_event=%s,
            revision=revision+1,updated_at=clock_timestamp() WHERE id=%s RETURNING *""",
            (incoming["id"],answer["id"])).fetchone()
        from .human_continuations import settle
        settle(store,c,actor,completed,"completed")
    else:
        need=need_for(c,store,actor,source["source"])
        change(c,need,"waiting",{**need["value"],"workId":str(work["id"]),
            "prerequisites":["result:"+str(work["id"])]})
    # Close the exact interpreted question before exposing its same-Work wake.
    # Other unresolved human prerequisites still keep this Work waiting.
    change(c,work,"pending" if work["state"]=="waiting" else work["state"],
        {**work["value"],"updates":updates,
         "resumeRevision":work["value"].get("resumeRevision",0)+1})
    return {"inputId":str(source["id"]),"workId":str(work["id"]),"state":"recorded",
        "objectiveReplaced":False,"writerSteered":False,"workRevision":work["revision"]+1,
        "responseRequired":not question}
