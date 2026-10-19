"""Bounded reader discovery using Hollywood's existing history/visibility query."""
from uuid import UUID
from .store import json_value
from .context_material import wire

def rooms(c,store,actor,value):
    after=str(UUID(value["after"])) if value.get("after") else None
    rows=c.execute("""SELECT r.id,r.name,r.purpose,r.owner FROM hb_room r
        WHERE r.authority=%s AND (%s OR EXISTS(SELECT 1 FROM hb_membership m
          WHERE m.room=r.id AND m.principal=%s AND m.can_read))
          AND (%s::uuid IS NULL OR r.id>%s::uuid) ORDER BY r.id LIMIT 21""",
        (actor["authority"],actor["administrator"],actor["id"],after,after)).fetchall()
    result={"rooms":[],"nextCursor":None,"meaning":"Visibility is not execution responsibility."}
    for row in rows[:20]:
        entry=json_value({**row,"purpose":row["purpose"][:256]})
        if len(wire({**result,"rooms":result["rooms"]+[entry]}))>5800:
            break
        result["rooms"].append(entry)
    if result["rooms"] and len(result["rooms"])<len(rows):
        result["nextCursor"]=result["rooms"][-1]["id"]
    return result

def history(c,store,actor,value):
    room=str(UUID(value["roomId"]))
    limit=value.get("limit",10);after=value.get("after");before=value.get("before")
    if type(limit) is not int or not 1<=limit<=20 or after is not None and before is not None:
        raise ValueError("Choose one history direction and limit1..20")
    if after is not None and (type(after) is not int or after<0):
        raise ValueError("Invalid after cursor")
    if before is not None and (type(before) is not int or before<1):
        raise ValueError("Invalid before cursor")
    view=value.get("view","conversation")
    latest=after is None
    page=store.history_in_transaction(c,actor,room,after or 0,limit,
        (before if before is not None else 9223372036854775807) if latest else None,view)
    result={k:v for k,v in page.items() if k!="events"}
    result["events"]=[]
    result["retrieval"]="Index only; context_material_read event:<id> for complete original content. Reuse cursors only for this room/view; permission is checked again."
    events=list(reversed(page["events"])) if latest else page["events"]
    for event in events:
        entry={**{k:event[k] for k in ("id","seq","room","author","recipient","kind","audience","source")},
           "reference":"event:"+event["id"],"preview":event["body"][:96]}
        if len(wire({**result,"events":result["events"]+[entry]}))>5800:
            break
        result["events"].append(entry)
    if latest:
        result["events"].reverse()
    if len(result["events"])<len(page["events"]):
        if latest:
            result["olderCursor"]=result["events"][0]["seq"]
        else:
            result["nextCursor"]=result["events"][-1]["seq"]
    assert len(wire(result))<=6144
    return result
