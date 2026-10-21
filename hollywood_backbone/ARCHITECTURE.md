# Hollywood communication authority

Hollywood owns rooms, authenticated principals, membership, scoped messages,
explicit response obligations and durable execution intent. LAX owns native
agent execution and contexts. NextComp is a client of this communication domain.
PostgreSQL is the canonical transactional service state; filesystem artifacts
remain host/workspace/version identified. A shared filesystem is not assumed.

An agent participates in multiple rooms with one persistent context by default.
Rooms describe a communication purpose and authorized audience. A room owner is
an accountable default addressee; it does not make the room an agent context or
prevent other agents from participating.

## Message and execution semantics

Passive messages retain actual human/agent authorship and never admit a model.
Explicit requests address an authorized agent and create a durable delivery.
LAX consumes verified peer deliveries as primary agent-authored input, separately
from human input. A passive history/tool read does not itself admit execution.

An agent can explicitly request a completion reply. Its terminal result and one
peer-completion delivery commit together in the original transition transaction.
That continuation cannot recursively request another acknowledgement. Completed
and failed results can return; explicit cancellation suppresses automatic
continuation. Portfolio supervision does not duplicate the requester's own
explicit reply obligation.

Membership and permissions are rechecked. Private source audiences cannot be
expanded. Original operation receipts deduplicate accepted commands; network
timeouts do not prove rejection or authorize repeating external effects. There
is no exactly-once execution claim.

## Agent context, responsibility and work

hb_agent_context identifies the agent's retained native thread and host/workspace.
hb_agent_execution captures each delivery generation, original claim and current
role revision without a synthetic role-goal dependency. Role/home-room changes
at idle retain this identity and pending work. Unsettled execution blocks role
activation until that original execution is accounted for.

Business goals, cooperation policies, work plans, assignments, artifact submission
and independent review organize accountable work. They do not select another
native agent context. Completed execution and accepted work are separate facts.
Changing a role or writing handed-off in a message does not transfer outstanding
work ownership.

## Authentication and execution boundaries

Native source v2 binds the authenticated agent, host, original delivery/claim/
generation, native thread/turn, tool call, current role revision and persistent
context. Independent host observation attests the original executor. Stale or
cancelled executions cannot acquire new effect authority.

External source agents use explicitly administrator-enrolled communication
credentials. Room grants and optional request authority still apply. Placed
native agents cannot enroll their execution credential in that external path.
This mechanism grants neither host execution nor administrator authority.

Global starts with actual installation administration. A role label cannot grant
that authority. Host control, account providers, repository routes and source
writer ownership must be configured and verified under their actual identities.

## Operation and migration

Migrations preserve canonical data. Legacy native goal binding data and all canonical
history remain available for older receipts and controlled recovery. The old
room-dependent context selector is not the current operating model.

Native unknown-delivery recovery reads the original execution and effects.
Cancellation records intent separately from observed stop. Communication-server
restart does not own or terminate agent lifetime. Idle observation and supervision
are deterministic; no recurring model polling is introduced.

Existing context adoption is explicit and retains every prior native history.
A runtime downgrade after new cross-room effects requires draining and explicit
context reconciliation; do not erase service state or replay requests to make
an older runtime appear healthy.

Admission pause and workspace identity: a same-host placement pause blocks new
execution admission and temporarily holds resource use. It retains the explicit
workspace routing choice and its revision. Re-enabling the same placement
restores routing only if current principal, root and grant authorization still
passes. Principal deactivation, enrollment deletion and host movement retain
permanent participation holds; migrations preserve existing holds until an
administrator explicitly reselects through the normal CAS API.

### Existing single-context agent enrollment

New purpose enrollment is an explicit drained protocol transition under the same
agent lock as legacy claims and execution-context binding. Legacy claim checks
its layout after acquiring that lock. An old worker that was waiting during the
transition cannot claim new work after the enrollment commits.

Enrollment refuses claimed, running, cancellation-requested or unknown original
deliveries, and any registered executor without verified stopped state. A
terminal delivery alone is insufficient resource-drain evidence. Pending input
is ingested only with generation zero, no claim/native thread/native turn, and
no cancellation request; uncertain legacy effects require their own settlement.
The original event/delivery IDs and legacy context/history are retained. Repeat
configuration of the same enrolled system preserves the existing reconciliation
contract; it does not start or restart any service.

A migration rehearsal must use an identified private state copy. Installing
filesystem services against a copy with production realm/agent IDs can collide
with the real installation namespace and is not an isolated runtime test. The
2026-10-21 copied-state proof preserves original rows and ACLs but does not imply
production, physical client or generalized enterprise-installation readiness.
Schema upgrades retain canonical data; they are not wholly reversible additive
changes (schema 33 changes executor uniqueness). After purpose work is admitted,
rollback must retain a compatible Hollywood API and original state, pause new
execution and reconcile exact receipts. Do not restore an old database snapshot
or old one-executor-per-generation runtime over new accepted work.
