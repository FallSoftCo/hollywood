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
Native tool data preserves peer authority separately from human input.

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

The schema is additive. Legacy native goal binding data and all canonical
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
