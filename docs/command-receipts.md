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

## Purpose context operation receipts

The separate authenticated route
GET /hollywood/v3/agents/{agent}/contexts/{purpose}/operations/{operationId}
reads an original purpose-context receipt. A current credential for that exact
agent and purpose is required. An absent committed receipt returns HTTP404 with
code CONTEXT_OPERATION_NOT_FOUND and mayApply=false. Wrong-purpose or invalid
credentials retain HTTP403. Absence of this metadata receipt is not proof that
a native or external effect did not occur.

Paired LAX may recover only an original bootstrap bind by first looking up its
receipt, then resubmitting the exact same idempotent binding metadata when that
authenticated lookup reports absence. It preserves the existing operation and
native-thread identities. Other uncertain context operations remain held.


### Declared room for a native human answer

AttentionCreate optionally accepts responsePublication={version:1,roomId:<UUID>}
for a nonsecret, nonblocking hollywood/question. Hollywood checks current
agent/human read and post access and appends an enforced room-visibility notice
to the displayed questions. The question digest binds the declared target.
Omitting the field preserves the private request and its original fingerprint.

An authenticated answer commits one existing humanQuestionAnswer event in that
room, with real human authorship and the original agent/context continuation.
It creates no peer delivery or execution permission. Its response_event is the
normal room-readable evidence pointer. Private question/approval controls keep
their participant-only access; room membership cannot read the private receipt.
Readers other than the original recipient receive ordinary room observations.

Original operation lookup returns the same response/event. Current permissions,
scope, cancellation and context are checked before publication. A denied reply
creates no partial room event. Cancelling later work does not erase an already
committed human communication. Approvals and generic callbacks cannot opt in.

A server downgrade to a version unaware of this opt-in must first settle or
explicitly withdraw pending publication questions; do not silently switch their
promised audience or replay answers. Current HTTP/PostgreSQL qualification uses
disclosed native/capacity metadata fixtures. Real native multi-context and
physical-client qualification are recorded separately.
