# Hollywood backbone candidate

Hollywood owns room and communication identity. No LAX process, model provider,
TUI or Android client is imported into the authority service. LAX owns the native
execution connector in the LAX repository, with a pinned app-server and local
intent journal. NextComp integration is a subsequent release gate.

## Communication and execution

A room has a stable authority/room identity, purpose and accountable agent owner.
Membership carries explicit read/post/request grants. `message` publishes to an
audience without admitting execution. `request` addresses an explicit agent or
the room owner and atomically persists its event and one execution delivery.
Observation through history/streaming does not create a context. Explicit agent
requests retain agent authorship and source references; the LAX connector injects
those as native tool authority rather than inventing a direct human instruction.

Every HTTP operation derives its principal from the bearer credential. Client
JSON cannot select an authoritative principal or enterprise. Administrators have
all room rights within their authority, including private visibility, matching
this deployment policy. Worker room grants do not grant host/filesystem access.
The Linux qualification installer now provisions separate Global and worker UIDs:
Global has verified sudo; the worker has a private workspace and state, native
workspace sandboxing and a service with `NoNewPrivileges=yes`. Independent checks
proved worker sudo denial and denial of Global credential reads. This does not
establish isolation between hostile enterprises sharing one unrestricted root.
Deploy those enterprises in separate VM/host security boundaries. Multi-human
access policy and shared product workspace capabilities still require qualification.

PostgreSQL commits events and execution intent together. The authority sequence
is allocated under an authority-row transaction lock, so replay order agrees
with commit order. Command IDs are scoped to the authenticated principal and a
canonical input digest. Changed input conflicts; retry resolves the original
accepted target. Receipts and history recheck current grants. Streams recheck
both room permissions and credential revocation.

Connectors must be enrolled for an agent and host. Concurrent claims serialize
per actor; only one pending delivery is admitted while that actor has claimed,
running, unknown or cancellation-pending work. Claim IDs and owner generations
survive server restart. No timeout, disconnect or absence of a process promotes
an unknown delivery back to pending. An unknown native effect requires native
readback/operator reconciliation. Current finite CLI refuses to replay it.

`cancelled` before admission and `cancel_requested` after admission are separate.
A connector acknowledgement can establish execution stoppage but never external
effect rollback. A live cancellation integration interrupted the exact native
turn, verified its owned helper process stopped and its deferred effect never ran.
Native turn completion is not business acceptance. Results/acceptance and actual
artifact evidence must remain separately attributable.

## Migration and release

The live v2 SQLite runtime is untouched. Before cutover, import its authority,
room and actor IDs, event/command aliases, bindings, paused flags and unconfirmed
receipts under a coordinated writer-release receipt. Never redispatch accepted
messages to repair their old recipients. Historical numeric events need explicit
alias mapping to stable v3 events; existing client receipt references must remain
resolvable. Do not turn imported membership into execution capability.

A pinned v3 compatibility boundary must preserve existing device pairing,
environment identity, drafts, unknown commands, question/approval scope and voice
origins while NextComp moves to the canonical API. No copied credential grants
another enterprise owner rights. No metadata mapping silently attaches a live
native thread; ownership release and native thread continuity are separate facts.

Bootstrap currently creates only the durable communication authority, one Global
room, and human/Global identities. Protected installation intent is fsynced before
DB mutation so retry retains identity and credentials. The candidate also
has packaged Linux Global/worker provisioning primitives. A real native Global
created the Engineering agent and room, provisioned the worker, delegated a task,
then observed and verified its actual artifact. Installation retry preserved a
deliberately stopped worker. The combined enterprise installer, doctor, backups,
upgrades, authenticated second-host enrollment and migration remain release gates.
Role metadata alone is not evidence of administrator execution capability.

## Primary responsibility and room awareness

Rooms are Hollywood domain objects in PostgreSQL, addressed by authority and room
UUID through its authenticated API. They are not NextComp pages or filesystem
directories. Filesystem trees remain canonical for source, work and artifacts;
room references must identify the authoritative host/workspace rather than imply
that every participant can open the same path. There is no reason to move the
user's current Development tree merely to organize communication.

An agent has a stable identity, primary role, responsibility, home room and role
revision. It may belong to many rooms with independently granted observation,
posting and request rights. Room purpose describes the work, and its owner is
accountable for default requests. Product and function discovery metadata and
pagination are still required beyond the current small catalog.

LAX captures `lax-agent-context/v1` for each admitted request: exact agent, sender
kind, authority, room purpose/owner, primary/home responsibility, source request,
audience, execution host/generation, response obligation and actual host authority.
It supplies this through native `turn/start.additionalContext`. Human bodies remain
native user input; agent bodies remain retained native tool output. No peer body
is placed in privileged role context. The server and execution runtime enforce
access and admission; prose instructions do not confer permissions.

Addressed work requires a response. Observation is silent unless an explicit
material-event policy creates a separately attributable escalation. Membership,
mentions and publications do not create execution. Global can discover and read
all rooms in its enterprise without acquiring a duplicate context in each room.
Bounded deterministic supervision and material-event policy are still needed for
autonomous enterprise operation; this candidate creates no recurring model wakes.

Historical source commit `468b24abd9549dece2e7a92e6353ea4d4bc43778`
contained typed Hollywood messages, primary/observed/wake room distinctions and
focused/ambient/broad attention. Its leader/reviewer/devil's-advocate coordination
document also contained proposals, which must not be described as shipped behavior.
The renewal's upstream native additional-context/tool-output APIs provide the
current injection boundary. Lost semantics are restored explicitly without
restoring old schedules or promoting peer text to developer authority.

## Evidence and limits

The disposable PostgreSQL/API scenario passed ten integration gates including
concurrent replay, private audience filtering, competing claims, cancellation,
restart retention and revoked access. Its execution acknowledgements were fixtures.
A separate actual LAX integration used three finite gpt-6.1-sol/xhigh turns:
Global delegated to Engineering, Engineering wrote and checked a real file and
published in its owning room, and Global read and summarized that report without
creating another execution context there. Both agents used native LAX app-server,
not a TUI. Receipts are in ../repair-release-20261014 relative to the workroot.

Additional actual integrations passed native cancellation, completion-reply loss
after database commit with original-turn reconciliation and an effect counter
remaining one, dedicated Global root execution, and packaged Global provisioning
of a resident Engineering worker. A passive publication created no delivery and
left the worker with only its idle connector process. Global reviewed its report
in its existing home context. Receipt files identify exact package wheels, native
turns, Linux identities, artifacts and limitations; none are phone/cutover proofs.

Native resume currently does not accept revised dynamic tool declarations. Existing
thread continuity across connector capability upgrades needs a native LAX change
and integration proof; creating replacement contexts silently is unacceptable.

Not yet qualified: host-wide capacity/fairness, native-process crash reconciliation
and unresolved-tool recovery, human questions/approvals, physical phone voice/push,
second-host authenticated execution/artifact retrieval, the full installer and
upgrade/backup/restore, migration/rollback, multi-human shared workspaces,
enterprise VM isolation, scalable subscriptions/availability and deployed cutover.
This candidate is not production-ready or an architecture-complete claim.


## Native activity and human control

Hollywood owns the room transcript. The authenticated native activity route
appends immutable phases bound to the original delivery, generation, host,
agent and native thread/turn. It derives the room and audience rather than
accepting those from the caller. Original log identities deduplicate; changed
payloads conflict. Activity never admits work or wakes observers.

LAX saves intent plus the native item in a protected filesystem journal before
publishing. A separate outbox retries only the same log record. Output/diff
excerpts are marked; raw read-tool payloads remain protected because access to
another room does not permit republishing its private history. Question tool
activity is private to the attributable human. NextComp reuses its session Runs
renderer, coalescing start/completion into one stable row while retaining both
underlying events and agent/host/context attribution.

Durable attention is bound to the originating source human, delivery generation,
native request slot and action digest. Questions require a human; Global/admin
can act on native approvals with its own identity. Response accepted, written
into the original RPC connection and execution completed are distinct facts. A
write is not proof of consumption. An uncertain result cannot replay an approval
into a new native process. Terminal execution closes or expires actionable old
prompts and preserves unknown response outcomes.

Actual qualification: authenticated PostgreSQL/HTTP restart and one real xhigh
LAX turn asking through Hollywood, receiving the exact answer in the same turn,
writing the proof file and publishing command phases. NextComp's Kotlin
projection consumed that actual feed. This is not physical-phone, native
approval, original-installation migration or production-cutover certification.

## Durable goal and work coordination candidate

Hollywood now has an additive goal/work schema and authenticated API. A goal
records its desired outcome, acceptance criteria, source request and accountable
room owner. Its revisioned work set and immutable prerequisite edges form its
plan. Creating this state admits no execution. Explicit assignment creates one
request/delivery atomically; dependencies require accepted work before admission.

Worker submission is fenced to the original delivery claim/generation and records
host/workspace-relative evidence references. It is not a native completion receipt
or a verified artifact download. Review requires completed execution and an
explicit authorized acceptance/rejection. Goal acceptance requires every work
item accepted and an attributable review statement. Actual artifact verification
and authorized cross-host retrieval remain further gates.

Pause prevents new work claims; it does not assert that active effects stopped.
Work cancellation records request versus settled cancellation separately. A goal
cannot become cancelled while its work has running, claimed, unknown or
cancellation-pending execution. Terminal goals cannot silently resume.

An explicitly configured review budget permits bounded review admission after
another worker's material report and terminal/unknown execution state. The
metadata dispatcher coalesces an outstanding review, persists causal deduplication
and spends the budget atomically. Ordinary chat, observation and a coordinator's
own reports cannot manufacture review loops. The opt-in LAX resident connector calls this metadata reconciliation endpoint;
only the explicit finite policy can admit a model review. Native idle goal
continuations are disabled before context load. Every admitted review retains
its source, claim and causal digest.

The supervision feed provides authorized event hints across rooms and a durable
CAS cursor. It includes execution activity without injecting all raw commands
into a model context. Room grants/private audiences remain authoritative on each
read. This primitive does not yet provide a complete Global attention policy,
backlog prioritization, or proven continuous supervision.

Current API qualification exercised actual PostgreSQL and authenticated HTTP:
dependency admission, separate submission/completion/acceptance, original
assignment receipt recovery across API restart, bounded review deduplication,
pause/resume/cancellation and durable supervision cursors. Its native execution
receipts were synthetic. It is not real-agent or production-cutover proof.

This first workflow contract accepts room-visible goal sources. Private-source
cross-room sharing must be explicit and is currently denied. Goal lists page by
stable UUID; a goal's plan returns a marked 100-item bound ; `GET /goals/{id}/work` provides permission-filtered UUID pagination.
`GET /rooms/{id}/work` discovers assigned work without granting access to its
parent goal room or disclosing its private objective/source. Editing/replacing
plans, optional/waived work and additional attempts remain unfinished. Original v2 work orders and accepted receipts have not been migrated.


## Native goal binding and observed enterprise proof

Schema version 3 adds a delivery/generation-fenced native goal binding with host,
workspace, context, native thread and goal instance identity. Current owner,
permissions and captured work/source are checked before binding. Native goal
observations must match that binding; activity derives canonical goal/work IDs
from the server record. Optional absent activity fields are omitted from receipt
fingerprints, preserving existing activity replay digests.

The packaged candidate completed an actual one-host enterprise goal: Global
provisioned a separate worker UID/room, delegated one assignment, the worker
received a human answer and wrote a real artifact, and Global independently
verified its SHA-256 and explicitly accepted work and goal. Three native turns
used two contexts and one policy-authorized review; no shadow Global context was
created. A fourth explicit human read-only follow-up published a structured
native plan. Native local completion and Hollywood business acceptance remain
separate facts. An ungranted human could neither read nor answer the question.

The API workflow probe additionally qualifies room work and goal plan pagination
through actual PostgreSQL/HTTP, without models. NextComp's candidate gateway
reads this actual native history and preserves identity across restart. These
proofs do not establish physical Android delivery, a second execution host,
continuous Global supervision, full cancellation/crash recovery, or production
cutover. Owned qualification agents are stopped after completion.


## Desired cancellation and authoritative recovery

Additive schema 4 retains `cancellation_requested` independently of observed
execution state. Requesting stop while delivery is unknown preserves unknown and
records the desired control. Repeating it does not imply execution stopped.
An original fenced native stop acknowledgement atomically cancels associated
work and advances its parent plan revision, under goal-before-delivery lock order.
Pending work can be cancelled before admission. Goal cancellation still refuses
to claim that active or unknown work has stopped.

Delivery readback includes the original native goal binding. LAX recovery checks
its context, workspace, native instance and objective digest before metadata
control; it never creates/adopts a replacement goal or starts a model turn.
Source-bound coordinator replies carry a separate `goalReferenceId`, so a
conversation reference does not manufacture a work assignment or silently retarget
an existing native goal. Current native coordination scope remains a release gate
for simultaneous business goals and control across multiple contexts/hosts.

Material supervision includes failed/unknown worker execution even when the
worker could not publish a report. The same explicit budget, causal digest,
outstanding-review coalescing and owner exclusion apply. It does not invent
worker evidence or authorize retry of an uncertain effect. Administrative room
inspection exposes all membership revisions to administrators and only the
caller's own grants otherwise. Role names still confer no authority.

Actual PostgreSQL/HTTP qualification covers unknown stop intent, stable repeated
stop, acknowledged work cancellation, source-bound goal references, current
membership revisions and pre-report failure review. Native cancellation/helper
and late-answer qualification is recorded separately; a partially executed or
failed probe is not a production release gate passing.


Schema 5 makes a goal stop an asynchronous aggregate operation. It closes new
admission, cancels pending work and records desired stop for active/unknown
owners in one transaction. The goal remains cancel_requested until original
acknowledgements establish that no active/unknown work remains. The final
acknowledgement closes the goal and emits one service-generated state fact in
its owning room, causally attributed to the accepted control. It expands no
private child body and admits no model. Known completed effects remain facts;
accepted work and original receipts/artifacts are retained.

The installed dev6 candidate passed an actual three-turn Global/worker exercise:
Global recovered a deliberately stale membership revision, provisioned a separate
worker, delegated a helper plus native question, handled the human stop in the
same context/goal instance, and closed the goal. The helper stopped without its
deferred effect, both bound native objectives paused, and the late answer was
rejected. API qualification separately covers direct goal-stop fanout across
running and pending work. These are one-host candidate proofs; production,
physical phone, all-context role lifecycle and enterprise migration remain gates.


## Native responsibility scopes

Business goals, dependency plans and acceptance belong to Hollywood; they are not
the single native goal of a supervisory context. Agent role metadata declares
native_goal_scope (role or work). Global bootstrap uses role; other primaries
may be explicitly configured by an administrator, without granting extra access.

A role-scoped delivery binds no business goal/work ID. Its current role revision
is checked, and hb_native_role_goal retains one native instance for that agent
and private context, including host, workspace, room/audience and direct sender.
The per-delivery binding remains immutable. Stale role revisions, changed native
instances or substituted workspaces are conflicts, not automatic migrations.
Assigned work must still bind its own goal/work and cannot claim a role instance.

Business acceptance/cancellation changes only its canonical business lifecycle
and child execution. It never completes or suspends the supervisor's broader
native responsibility. LAX retains native accounting and plan notifications while
the external role controller owns goal lifecycle and every execution admission.
Role objectives do not create timer/model continuation loops.

Schema 6 adds explicit role/binding scopes and the retained role-instance record.
Existing coordination bindings keep their digest/identity and remain historical
facts. No live context is adopted, cleared or replaced by migration. Converting
existing contexts and exposing general role stop/resume remain explicit gates.


Role protocol upgrade and rollback: schema 7 moves role/binding metadata into a
separate table, retaining the original ten-column binding row shape and every
recorded digest. Claims and automatic review dispatch for role-scoped actors
require the native-role-goals/v1 connector capability. A missing capability
rejects admission without consuming work/review policy; original receipts remain
readable through their authenticated read routes.

An older server can read legacy binding records, but cannot execute new role
semantics safely. Rollback therefore requires draining/stopping role writers and
disabling their placements before switching the authority runtime. Preserve the
database and new history; do not restore an old dump over accepted new work or
start an old coordinator against a role context. Use the coupled current package
to reconcile original receipts. Production migration remains held.


## Summer cooperation port: goal-scoped independent peers

The original July implementation and reports remain the behavioral baseline.
The canonical v3 port now derives a new goal's accountable planner from the
addressed source request, rather than silently replacing that peer with the room
owner. Historical goals keep their original accepted ownership. Goal creation
must belong to that exchange's author/recipient or an enterprise administrator.

Schema 8 adds a revisioned cooperation contract per goal, not one permanent
organization per room. The first supported canonical modes are `leader_award`
and `kanban_pull`. An explicit executor set and optional independent verifier
require existing current room grants; a role name grants no access. Directed
work retains explicit assignment. Kanban work can be genuinely unowned while
planned; an eligible peer claims it through one transaction which fences the
work revision, checks accepted prerequisites and queues one separate native
assignment. Claiming does not turn an unrelated current turn into implementation.
One peer cannot accumulate unsettled claimed work. Unknown or active work cannot
be stolen on lease expiry. Reassignment/handoff remains an explicit future port.

A designated verifier receives the existing finite material-report review budget
and can review work and accept the goal. It cannot rewrite the plan, assign work
or cancel the planner's goal. Planner/self acceptance is denied under that split;
enterprise administrators retain their intended override. The verifier must read
and independently check actual artifact evidence. Native completion, submission
and business acceptance remain separate states.

Policy/executor changes after any work admission are held for explicit migration.
New contracts require `cooperation-plan/v1` on native claim and review admission.
LAX captures the authenticated goal policy/revision and its own scoped team role
in native context. Publications and configuration emit facts, not model wakeups.
Existing goals without a contract retain the previous explicit-assignment path.
Rollback must stop new-policy writers and preserve accepted history; an old
runtime must never be used to reinterpret open/claimed cooperation work.

Remaining summer ports include room default cooperation templates with captured
goal overrides, dual-command ownership leases, adaptive phase
selection, safe handoff/yield with original-attempt history, collaborative edit
plans tied to actual source revisions and workspace/host identities, and broader
role-aware attention/quiescence. Evaluated alternative selectors remain evidence,
not a reason to discard the established cooperation substrate.

## Host admission and coalesced Global supervision candidate

Schema 10 adds host admission policy, a separate non-model service principal,
resource samples, connector presence and delivery admission traces. Admission is
mandatory for current connectors. An administrator enrolls the controller and
machine identity through PUT /hosts/{host}/admission; only that service
credential can submit POST /hosts/{host}/capacity. It receives no room,
placement or model permission. Requests use operation identities; an original
sample retry returns its original receipt without refreshing its observation.

POST /connector/observe reports the current eligibility, blocker and capacity
without claiming execution. Claims recheck under the host admission mutex and
require host-admission/v1 plus the current role/cooperation/attempt/briefing
capabilities. Capacity is bounded by the administrator ceiling, current CPU
capacity and sampled available-memory budget. This is admission budgeting,
not a hard per-process memory guarantee. Missing/stale samples hold admission.
Unresolved and cancellation-pending executions retain reservations until an
authoritative terminal observation. HTTP control does not wait for model slots.

Administrator-designated control agents can use reserved capacity for genuine
human requests; peer requests cannot claim that class. Worker admission rotates
across eligible room heads, excluding incompatible, disconnected, disabled,
busy or ungranted connectors. Durable traces separate receipt, eligibility,
admission, native running and terminal times. The service does not guess the
reason for a historical delay whose traces were never recorded.

Schema 11 adds one explicitly enabled portfolio supervisor per enterprise.
Only the installed administrator with Global role and its home room qualifies.
Activation starts at the current committed event head; historical work does not
resume. The deterministic reconciler coalesces material goal/work/decision
changes and execution incidents. Passive chat, native logs and Global's own
reports do not generate reviews. Current state eligibility precedes pagination.
One unsettled portfolio review and a finite administrator-owned window budget
bound admission; unchanged snapshots do not create more work.

Each portfolio request is direct Global-to-Global in its home room, with a causal
source and material digest. Source data keeps tool authority and private source
audiences do not expand. This intentional private portfolio context is separate
from the shared human Global context. Independent peer verification remains with
its designated verifier. Global reviews accepted results, blockers, execution
incidents or evidence for which it is the accountable reviewer.

GET /agents/{agent}/supervision exposes policy, current items and original
review execution receipts. Its continuation uses both afterReference and
afterScope. Dispatched does not mean reviewed, accepted or successfully acted on.
Unknown/stopped reviews retain their original receipts; unchanged material is
not automatic retry permission. Ordinary agent self-execution requests are
denied; bounded work claims and this explicit lifecycle policy are distinct
authorized admission mechanisms.

Qualification includes authenticated PostgreSQL/HTTP fault boundaries and real
native workflows. A six-turn load scenario exercised two held workers, human
Global control, acknowledged native cancellation, delivered artifact and one
automatic independent Global review. A newer coupled candidate passed a
four-turn Planner/Builder/Verifier workflow with actual host samples, a decision
revision and original native question across API restart. Both ran on one host;
their exact package pins and scope are retained in deployment evidence.
Synthetic native receipts in fault tests are explicitly labeled, never counted
as model execution.

This remains a candidate. Reserved capacity does not yet let a new human steer
an already executing Global turn. Multi-human privileged-operation provenance,
general role stop/reassignment, cross-host artifact capabilities, credential
separation, full restore and the physical NextComp journey remain release gates.
Rollback requires fencing current writers and preserving new history; schema
downgrade or restoring an old dump over accepted new work is not safe rollback.
