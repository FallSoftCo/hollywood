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
