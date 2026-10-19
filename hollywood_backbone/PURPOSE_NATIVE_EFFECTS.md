# Purpose-native effect authority

The v3 native source preserves authority, actor, host, original delivery/claim,
generation, request, current native thread/turn, tool call and argument digest.
It adds the immutable accepted execution scope ID and independently observed
executor ID. nativeGoalId is null: a purpose-native context does not need a
synthetic legacy goal binding.

Validation rechecks the authenticated purpose token, enabled placement, current
role revision, accepting writer turn, exact accepted scope, running inference
permit and independently live executor under the current enrolled controller.
It runs within the domain mutation's existing authority fence and transaction.
No application prompt, reader interpretation, room membership or model final
text grants this authority.

Exactly one physical executor may remain undrained for an original work claim.
Stopped incarnations remain immutable. A later physical incarnation is not
proof that prior effects were undone, and unknown work requires explicit
reconciliation. Resident purpose processes do not permanently consume inference
slots or block a later completed work claim.

Context-system enrollment is an administrative command with original-operation
receipts. An identical complete configuration is recoverable without resetting
the bindings or histories; a different configuration requires explicit migration.
Authenticated readback exposes binding IDs and credential digests, never tokens.

The v3 runtime protocol is a deployment fence: earlier purpose runtimes are
rejected before they can start work against this changed executor contract.
Legacy v1/v2 native effect sources retain their separate validation paths.
