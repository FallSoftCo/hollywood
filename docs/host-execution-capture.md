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
