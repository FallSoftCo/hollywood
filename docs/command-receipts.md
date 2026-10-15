# Original command receipts

Schema 13 records the incoming method, relative route and JSON body together with
the original accepted response in the same PostgreSQL transaction as a message,
workflow or decision. Request headers and bearer credentials are excluded.
Internal calls without HTTP wire evidence and older commands remain explicitly
unhydrated; current mutable state must not be substituted for the original result.

An authenticated GET /hollywood/v3/commands/{operationId} selects only the
caller's original command and applies current event/room visibility. Its existing
event and current-delivery fields remain available. The canonicalCommand object
separately identifies immutable authority, actor, operation, event, semantic digest,
wire request/digest and acceptedResult. evidenceComplete is false when original
evidence is missing. An accepted request can still have a cancelled current
delivery: acceptance and execution outcome are different facts.

LAX compares the original authority, actor, operation and complete wire evidence
before resolving uncertainty. Recovery reads the original command; it does not
resend an unknown mutation. No exactly-once execution guarantee is implied.

Scope: messages, business workflow and decisions. Administrative primitives and
partially completed host provisioning need their own protected receipts before
they can recover safely. Do not put enrollment secrets in this generic journal.

Qualification: tests/integration_command_receipts.py exercises the actual
authenticated PostgreSQL HTTP API, including restart, changed-operation conflict,
principal isolation, original versus current state and revoked room visibility.
