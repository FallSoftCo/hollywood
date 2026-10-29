# Awaited peer replies for single-context agents

An explicitly addressed request with `expectsReply` already produces one
canonical agent-authored `peerCompletion` notice. Single-context connectors may
advertise `single-context-peer-completions/v1` on their existing observation lane.
The authority then projects only eligible awaited completions into its existing
host-admitted delivery queue, using the original event and deterministic identity.
No new chat post, human input, endpoint, model polling or acknowledgement loop is
created. `UNIQUE(event,agent)` deduplicates reconnect/restart observation.

The original requesting agent/context, answered peer delivery, result, source
request, cancellation and current room permissions must match. Purpose runtimes
are excluded even when disabled; observing generic activity or joining a room
never schedules inference. Eligibility is filtered before the bounded queue page.

Execution uses the same persistent native context and `hollywood/agentMessage`.
The native v1 message/request enum projects this protocol result as an agent
message while retaining the canonical event, author, body and result source.
Original records are not rewritten and no user turn is fabricated. The server
returns a verified `lax-peer-completion/v1` origin; the connector rejects an
unverified observation as execution input.

The qualification receipt covers real PostgreSQL lineage and queue admission,
using rollback-only claimed metadata for the role/codec check. It does not claim
provider execution; the separate real cross-host native journey must also pass.
