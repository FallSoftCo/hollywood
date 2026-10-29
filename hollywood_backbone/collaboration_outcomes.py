"""Authenticated collaboration rejections are durable evidence, not effect grants."""
from psycopg.types.json import Jsonb
from .store import Conflict, digest, json_value

SCHEMA = "hollywood-collaboration-rejection/v1"


class CollaborationConflict(Conflict):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code

    @property
    def public_code(self):
        return "COLLAB_" + self.code.upper()


class RecordedRejection(Conflict):
    def __init__(self, receipt):
        super().__init__(receipt.get("message", receipt["error"]))
        self.receipt = receipt


def original_result(value):
    if value.get("schema") == SCHEMA and value.get("state") == "rejected":
        raise RecordedRejection(value)
    return value


def record_rejection(store, c, actor, source, payload, path, room, error, semantic_digest):
    # Called only after native participation, owned Work and physical-target
    # validation. The failed command's savepoint has already rolled back.
    from .collaboration import action_reference, protocol_audience
    result = json_value({
        "schema": SCHEMA, "state": "rejected", "httpStatus": 409,
        "operationId": payload["operationId"],
        "error": error.code if path == "collaboration/edit-plans" else str(error),
        "message": str(error), "code": error.public_code, "source": source,
        "mayApply": False, "retry": "read-original-operation-or-reconcile",
        "requestRejected": True, "newExecutionAdmitted": False,
        "newMutationPermitIssued": False, "priorFilesystemEffects": "not_inferred",
        "automaticRetryAuthorized": False,
    })
    event = store.event(c, actor, room, "progress",
        "Native collaboration rejected: " + str(error),
        source=source["requestId"], **protocol_audience(c, source),
        data={"type": "collaborationProtocol", "phase": "rejected",
              "nativeAction": action_reference(source, payload["operationId"]),
              "rejectionReceipt": result})
    result["eventId"] = str(event)
    c.execute("UPDATE hb_event SET data=jsonb_set(data,'{rejectionReceipt}',%s) WHERE id=%s",
              (Jsonb(result), event))
    wire = {"method": "POST", "path": path, "body": payload}
    c.execute("""INSERT INTO hb_command
        (authority,principal,operation,digest,event,wire_request,accepted_result)
        VALUES(%s,%s,%s,%s,%s,%s,%s)""",
        (actor["authority"], actor["id"], payload["operationId"], semantic_digest,
         event, Jsonb(wire), Jsonb(result)))
    return result
