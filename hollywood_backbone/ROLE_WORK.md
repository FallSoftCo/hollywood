# Event-backed work under an ongoing role

An agent's role outlives individual requests. Completing a delivery does not
withdraw that role, and an ongoing role is not a permanently open task.

The existing administrator-owned role has native_goal_scope=role. Its purpose
defines responsibility; existing room/host/resource grants still limit effects.
A role name, room membership or an incoming claim never creates permissions.

The native reader can call initiate_role_work after reading a pending ordinary
room observation and the exact current role. It supplies a bounded objective,
acceptance and interpretation. The server checks role scope/revision, source,
purpose, current turn, room grants and stop/cancellation state. Whether the
proposed action is useful and within the semantic responsibility remains a
model decision; these checks do not mechanically prove that judgment.

Admission atomically records:

- A control event with data.type=roleWork, source evidence, agent authorship,
  reader context/turn, role revision and digest. It is not a human message,
  chat request to itself, or input fabricated for the reader.
- The normal canonical delivery used by claims, executors and cancellation.
- The same private Work/result handoffs used by accepted addressed requests.

The full role snapshot and relevance interpretation stay in private Work
material. They are not automatically copied into a room. The writer receives
the accepted Work, executes with existing native tools and effects fencing,
and supplies verified result material. The responder owns outward reporting.
The original completed human request stays completed.

Role revision/scope is checked again at writer admission and execution capture.
An observation is consumed once in its original transaction; original operation
IDs return original receipts and changed requests conflict. This does not
guarantee exactly-once filesystem, network or other external effects. Unknown
outcomes retain normal original-receipt recovery requirements.

Role work may create finite canonical Goals using the existing Goal/Work tools.
Goal origin recognizes actual native role admission, not arbitrary event flags.
Existing Goal prerequisites, cooperation and independent review are unchanged.
Simple finite role work does not require a new perpetual Goal or task ledger.

There is no new timer, model scheduler, semantic coordinator or fourth context.
New room input reaches the reader through existing event-backed delivery.
Unchanged facts should not create another task; this is tested with actual
models, rather than assumed from instruction text or deduplication.

## Current candidate limits

This is a candidate paired with the matching LAX reader and execution-context
contract. Native binding advertises roleWork=1; LAX requires it before model
inference. A new versioned bind operation preserves old immutable receipts and
the same native history identity during an upgrade. It is not a production
promotion or a general reliability result.

The first SDK112/H79 real-model attempt failed: Hollywood admitted native role
work, but its execution context and LAX role validator still recognized only
addressed requests. That original attempt remains failed/cancelled, with its
interrupted native turn and artifacts retained. SDK113/H80 adds a typed native
execution origin and strict validation of actual canonical role work. Arbitrary
observations remain unable to admit a writer turn. Requalification uses a fresh
cohort under the unchanged frozen scenario and retains every attempt.

Private observations cannot initiate new role work without an explicit sharing
contract. They can still inform already authorized unfinished work through the
existing audience-preserving path.

Human questions and approvals must not follow the triggering fact's author or
source chain as authority. Canonical Role has a nullable decision_owner human.
New installation bootstrap explicitly records its Owner for Global; existing
roles receive no inferred backfill. Native role administration can set, preserve
or clear it under existing revision fences. It grants no room, host or resource
rights.

Role work captures the exact role snapshot/digest privately. Native Attention
uses that captured decision owner, checks active same-authority human identity
and current room read/request rights, and never selects a fallback from evidence.
Old ownerless snapshots continue to reject. Ordinary human-request/delegation
question routing is unchanged. The authenticated answer resumes the same role
Work through the existing reader/update path; it is not a new task or writer
steer.

The SDK115/H81 interactive candidate uses schema34 in a new private QA database
only. Its additive schema has not been migrated into the live database. The
source selector is decisionOwnerId on the existing role API/native tool; omitting
it preserves the enrolled owner, explicit null withdraws future routing, and an
explicit UUID must be an active human in this authority. A current Work retains
its captured role snapshot; changing the current Role does not select a new
answering human for that Work. Existing inference/delivery revision fences still
apply to role changes.

For a later reviewed rollout, back up the canonical database and identified
release first, apply the additive server schema, and qualify the matching server
before activating SDK115 readers. The reader fails before inference when its
paired contract is absent. Do not reconstruct contexts, replay pending inputs,
infer owners for old roles, or rebuild immutable bind receipts. Existing
ownerless role-work snapshots require explicit reconciliation, not backfill.

Rollback retains the database and original receipts, including explicit role
ownership. Do not drop the additive column or erase accepted questions/answers.
Drain the candidate's active inference and inspect unsettled work before choosing
the previous paired runtime; older servers refuse role-origin question routing.
Hold or explicitly reconcile that work rather than replaying it under inferred
authority. Bounded actual-model evidence and release limitations are separate.

Non-Global ongoing roles can be enrolled through the existing authenticated
role API or the native hollywood_set_role tool with nativeGoalScope=role.
Existing room/host/resource grants still apply.

The paired real-model r242 study retains both baseline and candidate outcomes,
all setup/fixture errors, exact inputs and controls. Its bounded local software
scenario is not multi-host, phone, installation or autonomous-business proof.
