"""Canonical execution parents are independent of conversational reply sources.

New relations commit with the original request. The migration may recover old
relations only from rows still provably belonging to that same PostgreSQL
transaction; a later retry cannot retarget an accepted child. Missing legacy
evidence holds instead of guessing from timestamps or local agent transcripts.
"""
import json

from psycopg.types.json import Jsonb

from . import native_effects
from .store import Conflict, Denied, digest, json_value

SCHEMA = "hollywood-native-delegation/v1"
PARENT_FIELDS = ("authorityId", "agentId", "hostId", "deliveryId", "generation",
                 "claimId", "requestId", "contextId", "roleRevision")


def record(c, actor, event, child_delivery, operation, wire):
    source = native_effects.delegation_source(c, actor)
    if source is None:
        return  # Explicit external communication has no native execution parent.
    effects = c.execute(
        """SELECT sequence FROM hb_native_effect WHERE authority=%s AND principal=%s
           AND operation=%s AND source=%s AND method='POST' AND path=%s
           AND wire_digest=%s AND wire_body_captured LIMIT 2""",
        (actor["authority"], actor["id"], operation,
         Jsonb(source),
         "rooms/" + str(event["room"]) + "/messages", digest(wire)),
    ).fetchall()
    if len(effects) != 1:
        raise Conflict("Original native delegation provenance unavailable")
    c.execute(
        """INSERT INTO hb_native_delegation
           (authority,child_delivery,parent_delivery,parent_generation,native_effect,
            operation,recorded_by) VALUES(%s,%s,%s,%s,%s,%s,'native_acceptance')""",
        (actor["authority"], child_delivery, source["deliveryId"],
         source["generation"], effects[0]["sequence"], operation),
    )


def read(store, actor_id, child_delivery):
    with store.pool.connection() as c:
        actor = store.actor(c, actor_id)
        child = c.execute(
            "SELECT * FROM hb_delivery WHERE id=%s AND authority=%s",
            (child_delivery, actor["authority"]),
        ).fetchone()
        if not child:
            raise Denied("Delegation unavailable")
        event = store.readable_event(c, actor, child["event"])
        if not actor["administrator"] and actor["id"] != event["author"]:
            raise Denied("Delegation belongs to its author or administrator")
        if event["kind"] != "request" or event["data"].get("authorKind") != "agent":
            raise Conflict("Native agent delegation required")
        row = c.execute(
            """SELECT r.*,n.source,n.operation AS effect_operation,n.method,n.path,
                      n.wire_digest,n.wire_body_captured,cmd.wire_request,cmd.event,
                      cmd.principal
               FROM hb_native_delegation r JOIN hb_native_effect n ON n.sequence=r.native_effect
               JOIN hb_command cmd ON cmd.authority=r.authority AND cmd.principal=n.principal
                    AND cmd.operation=r.operation
               WHERE r.child_delivery=%s AND r.authority=%s""",
            (child_delivery, actor["authority"]),
        ).fetchone()
        if not row:
            raise Conflict("Original native delegation provenance unavailable")
        source = native_effects.source(json.dumps(row["source"]))
        expected_path = "rooms/" + str(event["room"]) + "/messages"
        if not all((
            row["event"] == event["id"], row["principal"] == event["author"],
            row["effect_operation"] == row["operation"], row["method"] == "POST",
            row["path"] == expected_path, row["wire_body_captured"],
            row["wire_request"] is not None,
            digest(row["wire_request"]) == row["wire_digest"],
            source["authorityId"] == str(child["authority"]),
            source["agentId"] == str(event["author"]),
            source["deliveryId"] == str(row["parent_delivery"]),
            source["generation"] == row["parent_generation"],
        )):
            raise Conflict("Native delegation provenance differs")
        parent = c.execute(
            "SELECT * FROM hb_delivery WHERE id=%s AND authority=%s",
            (row["parent_delivery"], actor["authority"]),
        ).fetchone()
        # Reauthorize the parent without returning its private content.
        parent_event = store.readable_event(c, actor, parent["event"]) if parent else None
        if not parent_event or any((
            parent["agent"] != event["author"],
            str(parent["host"]) != source["hostId"],
            str(parent["event"]) != source["requestId"],
            parent["generation"] != source["generation"],
            parent["claim_id"] != source["claimId"],
            parent["native_thread"] != source["nativeThread"],
            parent["native_turn"] != source["nativeTurn"],
            parent_event["seq"] >= event["seq"],
        )):
            raise Conflict("Historical delegation parent changed")
        if source["schema"] != "hollywood-native-source/v2":
            raise Conflict("Persistent native delegation binding required")
        from .agent_context import execution
        binding = execution(c, parent["id"], source["generation"])
        if not binding or any((
            str(binding["principal"]) != source["agentId"],
            binding["context_id"] != source["contextId"],
            binding["native_thread"] != source["nativeThread"],
            binding["role_revision"] != source["roleRevision"],
        )):
            raise Conflict("Historical native delegation binding changed")
        return json_value({
            "schema": SCHEMA, "childRequestId": event["id"], "childDeliveryId": child["id"],
            "conversationSourceEventId": event["source"],
            "parent": {key: source[key] for key in PARENT_FIELDS},
            "nativeCallId": source["callId"], "operationId": row["operation"],
            "proof": {"nativeEffectId": row["native_effect"], "recordedBy": row["recorded_by"]},
        })
