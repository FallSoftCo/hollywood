# Shared execution capacity

Worker/control reservations no longer divide the available execution pool.
`reservedInteractive` remains in old policy and receipt schemas for readback;
it is not subtracted from available capacity. Both ordinary agent requests and
awaited peer replies may use all available execution capacity. No policy rewrite
or restoration of stopped work is necessary to adopt this correction.

Existing explicit host ceilings and measured-memory admission are unchanged by
this narrow correction. Transport flow control is separate from execution.
Cancellation, disabled placement, unresolved native delivery and independently
unsettled executor checks remain effective. This does not make unknown operations
safe to replay or authorize background continuation.

Qualification: `tests/integration_peer_control_admission.py` uses the actual
isolated PostgreSQL QA policy with maximum 1 / legacy reservation 1. Both an
ordinary peer request and the original awaited completion are eligible in
separate rollback-only fixtures, without starting a model. Parent cancellation
and unresolved executor fixtures remain ineligible.
