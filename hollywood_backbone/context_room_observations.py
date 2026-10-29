"""Durable room observations, separate from requests, deliveries and replies."""
from uuid import UUID, uuid5
from psycopg.types.json import Jsonb
from .store import Conflict, Denied
from .context_system import item, change
from .context_peers import require_current_read

class ObservationAudienceDenied(Denied):
    """Safe guidance for an already-authorized observation/Work pair."""
    code = "PRIVATE_OBSERVATION_AUDIENCE"
    next_action = ("Record the private observation without workId/workRevision to retain its "
                   "relevance without sharing it into another responsibility. Do not copy its "
                   "content into a public or different private conversation without explicit "
                   "source-author authorization. This does not authorize execution or a reply.")


def enroll(c, actor):
    c.execute("""INSERT INTO hb_context_observation_cursor(agent,seq)
        SELECT %s,next_seq FROM hb_authority WHERE id=%s ON CONFLICT(agent) DO NOTHING""",
        (actor["id"],actor["authority"]))

def refresh(c, store, actor):
    # A first enrollment starts at the current head, not historical messages.
    # Subsequent offline traffic remains behind this durable ingestion cursor.
    enroll(c,actor)
    cursor=c.execute("SELECT seq FROM hb_context_observation_cursor WHERE agent=%s FOR UPDATE",
                     (actor["id"],)).fetchone()["seq"]
    pending=c.execute("""SELECT * FROM hb_context_material WHERE agent=%s AND kind='input'
        AND state='pending' AND value->>'roomObservation'='true' ORDER BY created_at LIMIT 32""",
        (actor["id"],)).fetchall()
    for row in pending:
        try: store.readable_event(c,actor,row["source"])
        except Denied:
            change(c,row,"unavailable",{**row["value"],"disposition":"Source access revoked"})
    count=c.execute("""SELECT count(*) AS n FROM hb_context_material WHERE agent=%s
        AND kind='input' AND state='pending' AND value->>'roomObservation'='true'""",
        (actor["id"],)).fetchone()["n"]
    if count>=32:
        return
    head=c.execute("SELECT next_seq FROM hb_authority WHERE id=%s",
                   (actor["authority"],)).fetchone()["next_seq"]
    rows=c.execute("""SELECT e.* FROM hb_event e WHERE e.authority=%s AND e.seq>%s AND e.seq<=%s
        AND e.author<>%s AND e.kind IN ('message','request')
        AND (e.data->>'authorKind') IN ('agent','human')
        AND e.data->>'type' IS DISTINCT FROM 'peerCompletion'
        AND (%s OR EXISTS(SELECT 1 FROM hb_membership m WHERE m.room=e.room
            AND m.principal=%s AND m.can_read))
        AND (%s OR e.audience='room' OR e.author=%s OR e.recipient=%s)
        AND NOT EXISTS(SELECT 1 FROM hb_context_material m WHERE m.agent=%s AND m.source=e.id)
        AND NOT EXISTS(SELECT 1 FROM hb_event p WHERE p.source=e.id AND p.recipient=%s
            AND p.data->>'type'='peerCompletion')
        ORDER BY e.seq LIMIT %s""",
        (actor["authority"],cursor,head,actor["id"],actor["administrator"],actor["id"],
         actor["administrator"],actor["id"],actor["id"],actor["id"],actor["id"],min(16,32-count))).fetchall()
    for e in rows:
        identity=uuid5(actor["id"],"room-observation:"+str(e["id"]))
        c.execute("""INSERT INTO hb_context_material(id,agent,kind,source,state,value)
            VALUES(%s,%s,'input',%s,'pending',%s) ON CONFLICT(id) DO NOTHING""",
            (identity,actor["id"],e["id"],Jsonb({"roomObservation":True,"responseRequired":False,
              "meaning":"Attributed room context, not an assignment, permission, or reply obligation."})))
    position=rows[-1]["seq"] if len(rows)==min(16,32-count) else head
    if position!=cursor:
        c.execute("UPDATE hb_context_observation_cursor SET seq=%s WHERE agent=%s",
                  (position,actor["id"]))

def record(c,store,actor,binding,p):
    v=p["value"]
    if set(v) not in ({"observationId","body"},{"observationId","body","workId","workRevision"}):
        raise ValueError("Observation and optional exact original work revision required")
    source=item(c,store,actor,v["observationId"],"input")
    require_current_read(binding,source,p["nativeTurn"])
    if source["state"]!="pending" or not source["value"].get("roomObservation"):
        raise Conflict("Pending passive room observation required")
    body=v["body"]
    if not isinstance(body,str) or not body.strip() or len(body)>4000:
        raise ValueError("Bounded relevance interpretation required")
    incoming=store.readable_event(c,actor,source["source"])
    result={"observationId":str(source["id"]),"responseRequired":False,"writerSteered":False}
    if v.get("workId"):
        work=item(c,store,actor,v["workId"],"work")
        require_current_read(binding,work,p["nativeTurn"])
        if work["revision"]!=v["workRevision"] or work["state"] not in ("pending","running","waiting"):
            raise Conflict("Original unfinished work changed")
        original=store.readable_event(c,actor,work["source"])
        d=c.execute("SELECT * FROM hb_delivery WHERE event=%s AND agent=%s",
                    (original["id"],actor["id"])).fetchone()
        if not d or d["cancellation_requested"]:
            raise Conflict("Original work cancelled")
        # Room context can inform another authorized ROOM responsibility. A
        # private source cannot silently expand to a public or different DM.
        if incoming["audience"]=="direct" and (
                original["audience"]!="direct" or incoming["room"]!=original["room"]
                or {incoming["author"],incoming["recipient"]}!={original["author"],original["recipient"]}):
            raise ObservationAudienceDenied("Private observation cannot expand its audience")
        entry={"observationId":str(source["id"]),"sourceEventId":str(incoming["id"]),
          "authorId":str(incoming["author"]),"authorKind":incoming["data"].get("authorKind"),
          "roomId":str(incoming["room"]),"audience":incoming["audience"],"interpretation":body}
        change(c,work,"pending" if work["state"]=="waiting" else work["state"],
          {**work["value"],"updates":(work["value"].get("updates",[])+[entry])[-16:],
           "resumeRevision":work["value"].get("resumeRevision",0)+1})
        result.update(workId=str(work["id"]),workRevision=work["revision"]+1,objectiveReplaced=False)
    change(c,source,"handled",{**source["value"],"interpretation":body,
      **({"appliedToWorkId":v["workId"]} if v.get("workId") else {})})
    return result
