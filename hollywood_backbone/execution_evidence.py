"""Explicit publication of selected authority-held execution evidence.

Only canonical, owned execution records are exported. Neither host files nor
private input/control payloads are accepted. Published snapshots remain readable
in the chosen message audience without granting access to their original scope.
"""
import json
from uuid import UUID
from .store import Conflict, Denied, digest, json_value

EVENT_TYPES = {"executionActivity", "executorState", "collaborationProtocol"}
CHECKPOINT = "lax-native-idle-context-installed/v1"
MAX_BYTES = 16384


def references(value):
    if not isinstance(value, dict) or set(value) != {
            "version", "deliveryId", "eventIds", "contextOperationIds"}:
        raise ValueError("Exact evidence reference fields required")
    if type(value["version"]) is not int or value["version"] != 1:
        raise ValueError("Unknown execution evidence version")
    if str(UUID(value["deliveryId"])) != value["deliveryId"]:
        raise ValueError("Canonical original delivery required")
    for key in ("eventIds", "contextOperationIds"):
        items = value[key]
        if (not isinstance(items, list) or len(items) > 8
                or any(not isinstance(x, str) or not 1 <= len(x) <= 128 for x in items)
                or len(set(items)) != len(items)):
            raise ValueError("Bounded unique original evidence references required")
    if not 1 <= len(value["eventIds"]) + len(value["contextOperationIds"]) <= 8:
        raise ValueError("Select between one and eight evidence references")
    if any(str(UUID(x)) != x for x in value["eventIds"]):
        raise ValueError("Canonical original event references required")
    return value


def delivery(c, store, actor, identity):
    row = c.execute("SELECT * FROM hb_delivery WHERE id=%s AND authority=%s AND agent=%s",
                    (identity, actor["authority"], actor["id"])).fetchone()
    if not row or actor["kind"] != "agent":
        raise Denied("Evidence publication belongs to the original executing agent")
    store.readable_event(c, actor, row["event"])
    return row


def checkpoint(c, actor, original, operation):
    row = c.execute("""SELECT result FROM hb_context_operation
        WHERE agent=%s AND purpose='writer' AND operation=%s""",
        (actor["id"], operation)).fetchone()
    value = row["result"] if row else {}
    if value.get("schema") != CHECKPOINT or value.get("status") != "recorded":
        raise Denied("Original installed checkpoint receipt required")
    prior = c.execute("""SELECT result FROM hb_context_operation
        WHERE agent=%s AND purpose='writer' AND operation=%s""",
        (actor["id"], value["snapshotOperationId"])).fetchone()
    snapshot = prior["result"] if prior else {}
    if (snapshot.get("schema") != "lax-context-rebuild-snapshot/v1"
            or snapshot.get("sha256") != value["nativeReceipt"]["snapshotSha256"]):
        raise Conflict("Original checkpoint snapshot no longer resolves")
    content = json.loads("".join(x["content"] for x in snapshot["chunks"]))
    if not any(x.get("source") == str(original["event"])
               and x.get("delivery_id") == str(original["id"])
               for x in content["obligations"]):
        raise Denied("Checkpoint does not contain this original responsibility")
    # The receipt is bounded metadata. Never export snapshot chunks, role,
    # instructions, optional retained inputs, or the native action payload.
    return json_value(value)


def snapshot(c, store, actor, value):
    refs = references(value)
    original = delivery(c, store, actor, refs["deliveryId"])
    if store.readable_event(c, actor, original["event"])["audience"] != "room":
        raise Denied("Private execution requires a separate explicit disclosure contract")
    evidence = []
    for identity in refs["eventIds"]:
        row = store.readable_event(c, actor, identity)
        if (row["source"] != original["event"]
                or row["data"].get("type") not in EVENT_TYPES
                or row["audience"] != "room"):
            raise Denied("Only selected room-visible original execution evidence may be shared")
        evidence.append({"kind": "event", "event": json_value(row)})
    for operation in refs["contextOperationIds"]:
        evidence.append({"kind": "contextOperation", "agentId": str(actor["id"]),
                         "purpose": "writer", "operationId": operation,
                         "result": checkpoint(c, actor, original, operation)})
    value = {"schema": "hollywood-execution-evidence/v1",
             "authorityId": str(actor["authority"]), "agentId": str(actor["id"]),
             "deliveryId": str(original["id"]), "sourceEventId": str(original["event"]),
             "records": evidence,
             "verification": "Hollywood copied original authenticated authority records. "
                 "Enrolled-host file observations remain host attestations, not independent filesystem proof."}
    wire = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    if len(wire) > MAX_BYTES:
        raise ValueError("Selected evidence exceeds16384 bytes; select fewer originals, never truncate")
    return {**value, "sha256": digest(value)}


def index(store, actor_id, identity, after=0, after_operation=""):
    with store.pool.connection() as c:
        actor = store.actor(c, actor_id)
        original = delivery(c, store, actor, identity)
        events = c.execute("""SELECT id,seq,data FROM hb_event WHERE authority=%s
            AND source=%s AND audience='room' AND seq>%s
            AND data->>'type'=ANY(%s) ORDER BY seq LIMIT 17""",
            (actor["authority"], original["event"], after, sorted(EVENT_TYPES))).fetchall()
        operations = c.execute("""SELECT operation,result FROM hb_context_operation
            WHERE agent=%s AND purpose='writer' AND operation>%s
            AND result->>'schema'=%s ORDER BY operation LIMIT 9""",
            (actor["id"], after_operation, CHECKPOINT)).fetchall()
        installed = []
        for row in operations[:8]:
            try:
                value = checkpoint(c, actor, original, row["operation"])
            except Denied:
                continue
            installed.append({"operationId": row["operation"], "schema": value["schema"],
                              "generation": value["generation"]})
        return json_value({"schema": "hollywood-execution-evidence-index/v1",
            "deliveryId": identity, "events": [
                {"eventId": x["id"], "seq": x["seq"], "type": x["data"]["type"],
                 "phase": x["data"].get("phase"), "itemId": x["data"].get("itemId")}
                for x in events[:16]],
            "nextAfter": events[15]["seq"] if len(events) > 16 else None,
            "contextOperations": installed,
            "nextAfterOperation": operations[7]["operation"] if len(operations) > 8 else None,
            "meaning": "Select explicit references for request_peer evidenceReferences. "
                "Reading neither publishes evidence nor grants original-scope access."})
