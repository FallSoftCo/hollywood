# Native parent authority at mutation acceptance

An agent business mutation carries X-Hollywood-Native-Source compiled by LAX
from the captured tool scope. Hollywood checks authority, principal, enabled host
placement, original delivery/claim/generation/request, native thread/turn, current
role revision, native goal/context binding and the independently live executor
controlled by the current enrolled host controller. It rejects missing or stale
sources before domain mutation. Human actions retain ordinary authentication
and domain permissions.

An authority advisory transaction fence is acquired before principal/domain
locks. Ordinary effect transactions share it; cancellation, ownership,
permissions, goal/work lifecycle and controller changes take it exclusively.
The check, business mutation and redacted provenance insert use the same
PostgreSQL transaction. No network/model/shell wait belongs inside it. Shared
normal effects remain concurrent; authority-wide cancellation/lifecycle writes
briefly wait for accepted SQL transactions. Already admitted external effects
are not undone. Cross-enterprise fences are independent.

Metadata protocols retain their existing scoped checks and can reconcile after
execution stops. Reading an original command never invokes it again. Accepted
provenance is available at GET deliveries/{id}/native-effects with current
audience/authority authorization and a durable paginated cursor. Failed domain
transactions leave no accepted provenance. The short enterprise audit-counter
lock orders committed receipts and prevents a replay cursor skipping a slower
transaction. Its separate counter avoids reversing the domain event-row lock
order. This serializes native-effect SQL acceptance within an enterprise,
as event acceptance already does; it does not hold a lock during native work.
Cross-enterprise acceptance remains independent. Wire/argument digests are retained;
credential-bearing bodies are excluded. Existing semantic command digests and
accepted results are unchanged.

LAX waits for the authoritative running/native pointer receipt before executing
dynamic callbacks or creating native questions. Its reader can continue
receiving the original turn/start acknowledgement while callbacks wait.

This is native parent provenance, not cryptographic attestation that a model
chose particular tool arguments. Global intentionally retains installation
administration capability. A malicious enterprise Global requires an OS/VM
security boundary. Direct in-process Store use is a trusted operator path and
takes an exclusive fence; raw maintenance SQL must follow the same lock order.
Connectors require native-effect-source/v1 before claiming work. Deployment
must upgrade the API and connector together.

This slice does not admit a new human control turn after unknown work, transfer
the current session, qualify two execution hosts or release the phone.

Schema18 preserves earlier schema17 evidence and marks its body capture
incomplete. New receipts include wire_body_captured=true. Upgrade drains older
provenance writers before activating the new cursor contract.
