# Interrupted-session migration contract proposal

Scope: one eligible session interrupted by the system76-pc migration, followed by
review before expansion. Discovery, copied transcripts and room membership confer
no execution authority. Existing Global operation and accepted receipts remain in
place. This proposal is not an implemented activation API.

## Stable identity and room mapping

Identify the source using host identity, canonical runtime/account-state identity,
thread ID and interruption evidence. Record a source inventory fingerprint; do not
identify a session by its display name. LAX must establish the authoritative source
owner and whether any writer remains active. Preserve account, model, permissions,
history provenance and pending command/tool receipts as distinct facts.

Map that source identity to exactly one destination runtime owner, team, existing
room/task and registered actor. A room association is metadata only. Record the
public human migration request and one stable migration ID. A conflicting target
or changed source fingerprint under that ID must conflict rather than remap.

## Receipt sequence to agree with LAX

1. `discovered`: inventory only; record eligibility evidence, unresolved effects,
   source owner and proposed destination. No binding or model admission.
2. `release_requested`: persist intent and idempotency key before asking the
   authoritative source owner to relinquish execution.
3. `source_released`: require a verifiable source receipt tied to migration ID,
   source identity, destination owner and a fencing generation. A stopped process,
   absent network connection or copied files alone are insufficient proof.
4. `activation_requested`: persist intent before destination admission. Require
   the source fence, verified history/config import and reconciled pending effects.
5. `destination_active`: retain a destination receipt matching the source fence,
   original source identity, mapped actor/room and destination thread identity.
   Destination activation must be idempotent and queryable by migration ID.

Unknown release or activation outcomes remain explicitly unknown until the owning
runtime provides authoritative readback. Never infer failure from timeout and
reissue execution under a new ID. Uncertain tool effects require receipt/effect
reconciliation before continuation; they must not be replayed automatically.
The ledger can record facts but cannot itself provide a cross-host execution fence.

## One-session qualification gate

LAX inventory must identify a session with explicit interruption evidence and
known execution ownership, whose pending effects can be reconciled. Exclude
archived workers, schedules, completed sessions and any session whose source
cannot be fenced. Check that the target room and owner match the human scope.
Qualify duplicate requests, changed mapping, source-release timeout, activation
timeout, receipt mismatch, restart readback and attempted second owner in isolated
fixtures before any real activation. Report fixture evidence separately from an
actual migrated session. No eligible live session has yet been established here.

## Rollback and dependencies

Retain the accepted ledger, messages and receipts during code rollback. If source
release succeeded, do not reactivate it merely because destination activation is
unknown. Obtain destination release/fencing proof before transferring ownership
back. No simultaneous source/destination activation is permitted.

LAX owns source inventory, fencing, release/activation and receipt authenticity.
Hollywood owns durable migration identity, target mapping and reconciliation
after that native contract is agreed. NextComp may display those states without
turning metadata associations into writer attachment. Global reviews the concrete
single-session candidate and continuity sequence before rollout.
