# Windows federation node

The node serves the same authenticated room/message API and PostgreSQL schema as
other hosts. NATS provides authenticated node discovery and routed transport;
Hollywood persists communication and receipts. LAX, not Hollywood, owns native
threads, model turns and local execution. Room membership does not allocate a
worker or create an execution reservation.

The qualified Windows deployment uses an isolated loopback PostgreSQL cluster
with SCRAM authentication, a loopback node API and the existing TLS NATS fabric.
It uses a fresh source-scoped NATS identity. Agent/host reference enrollment
copies metadata and token digests, never another agent's bearer credential.
Cross-authority visibility remains separately authorized.

## Credential boundary

Install the enterprise dependencies, including the Windows-only pywin32 marker.
`federation_runtime.load` uses `windows_credentials.read_private_text` on Windows
and preserves the existing POSIX mode checks on other systems.

The Windows reader opens the ordinary file without following a reparse point,
checks its owner SID and current-user-only DACL on that same open handle, reads
at most 4,097 bytes and rejects content beyond the 4,096-byte bound. It does not
repair an existing unsafe ACL. A POSIX-looking `chmod(0600)` is not evidence of a
private Windows credential.

Actual Windows qualification checked both a private credential and rejection of
an inherited/default ACL. The live node then passed authenticated local reads,
six-node NATS discovery and source-scoped room transport. The separate native
LAX journey executed a local Windows tool and returned one attributable answer
through NextComp. No model calls are needed for discovery or metadata enrollment.

## Operations and limits

Use the owning host's explicit desktop-user startup and bounded recovery path.
The legacy POSIX bootstrap/file-writing CLI is not qualified by the Windows
server test; provisioning uses the existing Store API and native private files.
Do not create a second database, agent or accepted request to probe an uncertain
original startup or delivery.

NATS reconnection does not establish execution failure or permission to replay an
external effect. Inspect the original operation ID and retain unknown outcomes.
Redundant brokers do not replicate each node's PostgreSQL room storage. An
offline room owner must remain explicitly unavailable rather than presenting
an invented replica or silently transferring ownership.
