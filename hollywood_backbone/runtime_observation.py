"""Authenticated host software facts may continue the same idle accepted work.

This is native lifecycle metadata, not a human/agent message or new assignment.
The enrolled controller attests its protected local installation. No model,
process launch or external-effect retry occurs inside this transaction.
"""
from .store import Conflict,Denied,json_value
from . import admission
from .executors import controller
from .context_system import change


def observe(store,actor_id,host,agent,payload):
    with store.pool.connection() as c:
        actor=store.actor(c,actor_id)
        policy=controller(store,c,actor,host)
        if payload["machineIdentity"]!=policy["machine_identity"]:
            raise Conflict("Observed installation belongs to another host")
        fingerprint,old=admission.command(c,actor,payload["operationId"],
            {"kind":"native-runtime-observation","host":host,"agent":agent,"payload":payload})
        if old:return old
        admission.lock(c,host)
        system=c.execute("""SELECT s.* FROM hb_context_system s
            JOIN hb_principal p ON p.id=s.agent AND p.active
            JOIN hb_placement l ON l.agent=s.agent AND l.host=s.host AND l.enabled
            WHERE s.agent=%s AND s.host=%s AND s.authority=%s AND s.enabled""",
            (agent,host,actor["authority"])).fetchone()
        if not system:raise Denied("Enabled installed native agent on this host required")
        work=c.execute("SELECT * FROM hb_context_material WHERE id=%s AND agent=%s AND kind='work' FOR UPDATE",
            (payload["workId"],agent)).fetchone()
        if not work or work["revision"]!=payload["workRevision"] or work["state"] not in ("pending","running","waiting"):
            raise Conflict("Exact current unfinished work required")
        d=c.execute("SELECT * FROM hb_delivery WHERE event=%s AND agent=%s FOR UPDATE",
            (work["source"],agent)).fetchone()
        if (not d or d["state"] not in ("claimed","running") or d["cancellation_requested"]
                or str(d["host"])!=host or d["native_turn"]!=payload["priorNativeTurn"]):
            raise Conflict("Original work no longer admits this native continuation")
        if c.execute("SELECT 1 FROM hb_purpose_context WHERE agent=%s AND purpose='writer' AND active_turn IS NOT NULL",(agent,)).fetchone():
            raise Conflict("A runtime update cannot steer a running writer")
        if not c.execute("""SELECT 1 FROM hb_context_permit WHERE agent=%s AND purpose='writer'
            AND native_thread=%s AND native_turn=%s AND state='released'
            AND terminal_status IN ('completed','interrupted','failed')""",
            (agent,d["native_thread"],d["native_turn"])).fetchone():
            raise Conflict("Original inference terminal receipt required")
        observation={"schema":"lax-native-runtime-observation/v1","authorityId":str(actor["authority"]),
            "agentId":agent,"hostId":host,"controllerId":str(actor["id"]),"authorKind":"service",
            "operationId":payload["operationId"],"installationDigest":payload["installationDigest"],
            "laxVersion":payload["laxVersion"],"hollywoodVersion":payload["hollywoodVersion"],
            "runtimeProtocol":payload["runtimeProtocol"],"availableCapabilities":payload["availableCapabilities"],
            "changeReason":"software_upgrade","priorNativeTurn":payload["priorNativeTurn"],
            "meaning":"Protected host installation changed. This is a capability observation, not a human instruction, permission grant, completed peer, or evidence that prior external effects were absent."}
        value={**work["value"],"runtimeUpdates":(work["value"].get("runtimeUpdates",[])+[observation])[-8:],
               "resumeRevision":work["value"].get("resumeRevision",0)+1}
        change(c,work,"pending",value)
        return admission.finish(c,actor,payload["operationId"],fingerprint,{
            "kind":"native-runtime-observation","hostId":host,"agentId":agent,
            "workId":str(work["id"]),"workRevision":work["revision"]+1,
            "observation":observation,"originalObjectivePreserved":True,
            "modelsStartedByEndpoint":0,"humanOrPeerMessagesCreated":0})


def read(store,actor_id,host,agent,operation):
    with store.pool.connection() as c:
        actor=store.actor(c,actor_id)
        controller(store,c,actor,host)
        row=c.execute("SELECT result FROM hb_admission_command WHERE principal=%s AND operation=%s",
                      (actor["id"],operation)).fetchone()
        if (not row or row["result"].get("kind")!="native-runtime-observation"
                or row["result"].get("agentId")!=agent or row["result"].get("hostId")!=host):
            raise Denied("Original runtime observation unavailable")
        return row["result"]
