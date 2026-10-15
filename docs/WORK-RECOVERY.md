# Recovering a pre-turn admission failure

`POST /hollywood/v3/work/{work}/recover` and native `hollywood_work_recover`
record a manager's explicit reconciliation of a failed admission. They return
that same work to `planned`; a separate `assign` creates a fresh attempt.
They preserve the original specification, acceptance, dependencies, owner,
request, failure receipt and attempt lineage. Recovery is not work acceptance.

The request supplies the current work revision, exact prior delivery, failure
result event, independently stopped executor, `outcome=pre_turn_denied`, the
SHA-256 of the inspected reconciliation evidence, and an attributable note.
Only the authenticated administrator or accountable goal manager can act.
Native callers retain their live source, permission and lifecycle fences.
The accepted command and immutable recovery event/table commit together;
repeating the same accepted operation reads its receipt. New assignment uses a
new operation and attempt, never an implicit replay after a timeout.

Server checks require the exact current assigned attempt to be failed with no
native turn receipt, no cancellation, no report, no recorded native business
effects, and no unresolved attention/control. The failure result and root host
observation must match that attempt, generation, claim, agent and host. The
executor must have a positive independent stopped observation. Concurrent
cancellation, revisions or another recovery cannot bypass the authority fence
and revision compare-and-swap. Recovery evidence appears in attempt reads.

A stopped process or absent native turn pointer cannot prove that no external
effect occurred. The outcome is an explicit manager attestation after inspecting
the protected original launch/goal/submission journals and effect receipts.
The evidence hash binds that reviewed record; it does not remotely verify a
filesystem artifact or cryptographically attest the model's reasoning. Do not
use this action for an ambiguous turn submission or possibly executed effect.
Those require their original effect reconciliation path and remain held.

The first production use is the three ozzz startup attempts rejected before
model admission by the native 4,000-character goal limit. Their original journals
never reached native-submission-intent, their exact executors were independently
stopped, and the deterministic goal validation error was reproduced with zero
model turns. Full tasks remain in Hollywood; native goals now reference the
exact contract rather than duplicating long assignments and acceptance text.
