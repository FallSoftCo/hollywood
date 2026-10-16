# Authenticated agent effects

Native effects v2 use X-Hollywood-Native-Source with schema
hollywood-native-source/v2. The fields retain authorityId, agentId, hostId,
deliveryId, claimId, generation, requestId, nativeThread, nativeTurn, callId,
tool, argumentsDigest, roleRevision, contextId and nativeGoalId. The last field
is null: an agent execution no longer depends on a synthetic goal.

The server derives the principal from its credential, rechecks authentication
inside the authority mutation fence, validates the immutable agent execution
binding, current responsibility, original delivery/claim/generation and native
thread/turn, then requires the independently observed live attached executor.
Cancellation, revocation and obsolete ownership reject new effects.

Original native effect records and canonical command/administrative receipts
commit with accepted domain changes. Recovery inspects those original receipts
and never invokes the model or tool again merely because a reply was lost.

The v1 source and native-goal bindings remain supported for retained older
execution evidence. They do not define new persistent-agent execution.

An external source agent needs explicit administrator enrollment of its
communication credential, room read/post/request grants and an optional request
capability. Only the message transport is admitted through that enrollment.
Native placement disallows enrolling its execution credential through this
path. Human/service impersonation and bypass of a stale native source are
rejected. Transport enrollment has a durable administrative operation receipt.
