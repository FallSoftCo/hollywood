# Host-scoped native execution capture

`GET /hollywood/v3/executors/{id}?view=capture` adds a read-only capture snapshot
for the executor's currently enrolled host controller, or an existing authority
administrator. The default status response is unchanged. Agent credentials cannot
request this privileged host view merely by owning the delivery.

The controller must belong to the same authority and own this executor's host.
The delivery must retain the original agent, host, generation and claim. Current
role and delivery facts are read in one transaction with shared locks; stale
ownership returns a conflict. No credential or execution admission is returned.
A stopped executor can be inspected, but the snapshot cannot restart it or grant
new native work. Existing native mutation and independent observation guards
remain mandatory.

This removes a need for authority-wide credentials when reading an exact local
execution. It does not yet replace administrative installation or capture-grant
writes. Cross-host native integration remains a separate qualification.

## Current Work access without authority-wide credentials

An enrolled, active collaboration controller may use the existing physical Work
grant endpoint for its own host. The agent must already have current writable
access to that root and a persistent single-context owner on that host. The
original typed Work assignment, running delivery and independently observed
attached executor must still match. This carries existing access into that Work;
it cannot register roots or agents, restore a revoked grant, create an edit plan,
admit a model or authorize a filesystem mutation.

The current controller/bridge, root, agent access and native context are fenced
in the same transaction. Goal and Work eligibility are checked; actual edits
retain their separate live permission checks. Purpose-separated runtime grants
continue through their existing administrative path; this new service authority
is limited to persistent single-context execution.

Service callers need an original operation ID. Unknown replies reconcile the
same actor's accepted administrative receipt; an exact previously accepted
command returns that receipt without a second mutation, even after completion.
Other administrative actions remain denied. Tests use real HTTP/PostgreSQL and
explicit rollback-only synthetic execution facts, not a claimed native journey.
