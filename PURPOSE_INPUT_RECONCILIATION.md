# Original canonical inputs and repair

Canonical Work assignment/claim, bounded work review and enabled Global portfolio
review all ingest their generated request into an enrolled purpose inbox in the
same PostgreSQL transaction as the original delivery. Human and agent authorship
remain the original event's facts. Interpretation still precedes work acceptance;
neither ingestion nor a delivery receipt starts a native execution inside the
transaction.

For an already accepted enrollment, context-system configuration accepts the
optional reconcilePendingDeliveryIds list (at most100 distinct exact IDs).
The caller must retain the same host, logical workspace and all three context
credential digests. Only original pending generation0 deliveries with no claim,
native pointers or cancellation can be repaired. Canonical Work must still point
to that exact assignment/owner. Repair rechecks current room/post access,
ingests original event IDs idempotently and records a normal administrative
receipt. It never reposts, changes generation, creates a delivery, starts a
service or overrides a deliberate service stop. Unknown/admitted/cancelled
execution cannot use it. There is no implicit historical queue backfill on
normal reconciliation.

Active purpose readers also reuse the existing configured Global portfolio
reconciler. Explicit activation frontier, material filtering, budget, snapshot
deduplication and unsettled-review holds remain intact. A terminal causal Goal
already covered by an open original purpose responsibility links dispatch
coverage to that original request, avoiding a redundant self-review. Coverage
is not semantic review or acceptance; the original response remains owed.

The r38 HTTP/PostgreSQL proof is rollback-only metadata qualification. Actual
canonical team execution retains the original request, Work IDs and assignments
and must independently prove model behavior, edits, reports and acceptance.
