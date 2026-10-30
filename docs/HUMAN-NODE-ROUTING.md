# Human access to independently owned Hollywood rooms

Status: implemented candidate following dev160/SDK206; not yet installed.
Actual PostgreSQL/NATS/HTTP qualification is retained under qualification/viewer-*.
No human command federation is enabled merely by this document.

The existing HTTP API remains the user interface. Node-addressed NATS RPC is
inside Hollywood, not a second model command channel. A human identity must
originate in authenticated paired-device traffic; agent traffic stays on the
native agent transport and cannot assert human authorship.

A protected installation maps the same global viewer subject to an existing
local human principal at each receiving node. Reads retain current room/direct
permissions. Writes additionally require an explicit source-node command grant;
read-only viewer enrollment must never acquire write access by upgrade. Native
agent credentials and host-controller credentials cannot use the human domain.
Receiving transactions recheck current human identity and room permissions.
No bearer token crosses NATS. Provenance retains originating node/viewer and
receiving local principal rather than inventing a human author from model text.

Catalog identity is (organization, node), not organization alone. Every room and
agent retains its canonical node. Room histories, activity views and cursors are
independent per owner; source refresh does not become work recency. A missing
peer is unavailable, not idle. Local access must work without discovery or a
peer. Separate agent reference rows never become duplicate local agents.

Before a mutation crosses a node, persist its original actor, operation, target,
resource and content digest at the ingress. A lost response cannot select a new
target or submit a new operation. Receipt lookup follows that recorded target.
Receiver idempotency remains canonical; retries do not create another worker.
Apply the same binding to replies, cancellation and questions, not only messages.
Do not flatten independent inbox sequence cursors into one invented cursor.

The deployed system76 stock gateway currently has only the older v2 adapter;
its presence is not proof of v3 room support. Preserve that Codex environment and
the existing enterprise binding. A dedicated additive native development profile
can use the already-maintained v3 gateway, paired with a dedicated human identity
that sees development rooms rather than operational QA rooms. Pairing is a real
client authorization action; a new backend is not automatically a paired profile.

External developer handoffs remain agent-authored. An administrator may explicitly
enroll a same-authority human decision owner on a non-placed external agent's
communication credential. On authenticated request acceptance the server captures
that owner, after checking active identity and room read permission. Neither the
message body nor an agent's choice of labels supplies human authority. A later
question follows the immutable accepted attribution, even if the external grant
is retired. Human response admission still checks current identity, room access,
original delivery generation and native question scope. This grants no execution,
administrator privilege, automatic model activation or permission bypass.

Each NATS service admits a bounded number of I/O handlers and reserves read
capacity so a held mutation cannot block receipt reconciliation. These bounds
are transport load protection, not agent execution slots. Busy-before-dispatch
is explicitly retryable on the same original target/operation; ambiguous outcomes
remain held for the canonical receipt and never re-enter domain admission.

Required proof before moving this work: human catalog includes both same-org
nodes; latest/history/activity read the same canonical room; one request receives
one receipt after disconnect/reopen; direct audience and revoked grants remain
private; reply/steer/cancel/question stay bound to owner; temporary peer loss
keeps local work accessible. Then move implementation ownership through an
explicit written checkpoint, with no duplicate active developer or old request
replay. Multi-node capacity admission is a separate remaining requirement.

The existing inbox and inbox/pending HTTP reads accept an optional nodeId.
Each response retains that node's original counter. Consumers persist a cursor
per installation/viewer/organization/node; they never compare counters from two
nodes. Remote event references are cached in one SQL batch for canonical read
routing. The recipient alias may be mapped to the ingress human, while
sourcePrincipalId retains the receiving identity. Event authorship is unchanged.
Node-addressed reads require human enrollment and an explicitly discovered node
in the same organization. Agent and non-federated callers cannot silently fall
back to a local feed. A denied organization is not a network outage.
