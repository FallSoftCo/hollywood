"""Frozen inference facts for older scope/claim substrate checks.

These fixtures do not qualify capacity admission. integration_context_capacity.py
uses real authenticated HTTP and the actual admission policy for that evidence.
"""
from uuid import uuid4
from hollywood_backbone.context_system import lifecycle as native_lifecycle
from hollywood_backbone.store import digest


def lifecycle(c,store,actor,purpose,binding,p):
    value=dict(p["value"])
    if p["action"]=="begin":
        system=c.execute("SELECT * FROM hb_context_system WHERE agent=%s",(actor["id"],)).fetchone()
        identity=str(uuid4())
        c.execute("""INSERT INTO hb_context_permit
            (id,agent,purpose,host,context_id,native_thread,operation,fingerprint,material_ids,
             role_revision,queue_class,state) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s::uuid[],%s,'worker','reserved')""",
            (identity,actor["id"],purpose,system["host"],binding["context_id"],p["nativeThread"],
             "frozen-scope-fact:"+identity,digest(p),value["materialIds"],system["role_revision"]))
        value["permitId"]=identity
        if purpose=="writer":
            d=c.execute("SELECT d.* FROM hb_delivery d JOIN hb_context_material m ON m.source=d.event AND m.agent=d.agent WHERE m.id=%s",
                        (value["materialIds"][0],)).fetchone()
            if not c.execute("SELECT 1 FROM hb_executor WHERE delivery=%s AND generation=%s AND state<>'stopped'",
                             (d["id"],d["generation"])).fetchone():
                from psycopg.types.json import Jsonb
                controller=c.execute("SELECT controller FROM hb_host_admission WHERE host=%s",(system["host"],)).fetchone()
                c.execute("""INSERT INTO hb_executor(id,authority,delivery,agent,host,generation,claim_id,
                    descriptor,descriptor_digest,state,controller,launch_phase,reservation_descriptor,reservation_digest)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,'live',%s,'attached',%s,%s)""",
                    (str(uuid4()),actor["authority"],d["id"],actor["id"],system["host"],d["generation"],d["claim_id"],
                     Jsonb({}),digest({}),controller["controller"],Jsonb({}),digest({})))
    elif p["action"]=="end":
        permit=c.execute("SELECT id FROM hb_context_permit WHERE agent=%s AND purpose=%s AND native_turn=%s",
                         (actor["id"],purpose,p["nativeTurn"])).fetchone()
        if permit:
            value["permitId"]=str(permit["id"])
    return native_lifecycle(c,store,actor,purpose,binding,{**p,"value":value})
