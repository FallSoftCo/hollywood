"""Explicit repair of exact canonical pending inputs; never repost or execute."""
from uuid import UUID
from .store import Conflict

def reconcile(c,store,actor,agent,ids):
    if not isinstance(ids,list) or len(ids)>100 or len(set(ids))!=len(ids):
        raise ValueError("At most100 distinct original delivery IDs required")
    target=store.actor(c,agent)
    result=[]
    for identity in ids:
        identity=str(UUID(str(identity)))
        delivery=c.execute("SELECT * FROM hb_delivery WHERE id=%s AND authority=%s AND agent=%s FOR UPDATE",
                            (identity,actor["authority"],agent)).fetchone()
        if (not delivery or delivery["state"]!="pending" or delivery["generation"]!=0
                or delivery["cancellation_requested"] or delivery["claim_id"]
                or delivery["native_thread"] or delivery["native_turn"]):
            raise Conflict("Exact unadmitted noncancelled original delivery required")
        event=store.readable_event(c,target,delivery["event"])
        store.room(c,target,event["room"],"can_post")
        if event["kind"]!="request" or str(event["recipient"])!=agent:
            raise Conflict("Original addressed request differs")
        if event["data"].get("workId"):
            work=c.execute("SELECT * FROM hb_work WHERE id=%s AND authority=%s",
                            (event["data"]["workId"],actor["authority"])).fetchone()
            if (not work or work["owner"]!=target["id"] or work["state"]!="assigned"
                    or work["delivery"]!=delivery["id"] or work["request_event"]!=event["id"]):
                raise Conflict("Current canonical assignment differs")
        existed=c.execute("SELECT 1 FROM hb_context_material WHERE id=%s AND agent=%s AND kind='input'",
                           (event["id"],agent)).fetchone() is not None
        from .context_system import ingest
        ingest(c,agent,event["id"])
        result.append({"deliveryId":identity,"eventId":str(event["id"]),"inputAdded":not existed})
    return {"deliveries":result,"requestsReposted":0,"modelsStarted":0,"servicesStarted":0,
            "meaning":"Original attributable requests ingested. Reading/acceptance and normal native admission remain separate; stopped services remain stopped."}
