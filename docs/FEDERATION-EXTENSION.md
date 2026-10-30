# Hollywood federation: extend the transport

Status: candidate, not yet a deployed multi-host product.

Hollywood remains the application. Each instance keeps its existing API, store,
room authority, operation receipts and native agent lifecycle. Add one NATS
connection and a thin NATS Services adapter; do not replace the database or invent
a distributed scheduler. An authority identifies an organization, not a server. One organization can span independent stores; each store has a separately addressed node ID.

All enrolled instances on the owner's tailnet are discoverable. Connecting to
any instance assembles the same authorized room/agent catalog from those sources.
A room routes by its original authority/room ID; an agent retains its origin and
execution placement. Offline sources show unavailable without blocking other
instances or pretending cached data is fresh. Federation is not automatic
replication of an offline host's entire history or migration of its executors.

## Use existing mechanisms

* NATS Services API supplies discovery, health/statistics and queue-group service
  routing. `federation_service.py` adapts Hollywood handlers and their native
  source checks; it does not implement another membership/leader system.
* Core request/reply has one attempt. Lost replies remain unknown and use the
  original domain operation for reconciliation. No automatic command replay.
* PostgreSQL remains each existing instance's canonical transactional store.
  No Patroni/etcd/storage migration is part of this extension.
* Where offline transport delivery is necessary, JetStream plus a transactional
  outbox carries stable event references. Consumers remain idempotent and query
  canonical state with their own audience/cursors. A notice is never a model wake.
* Tailnet networking is the connectivity boundary. NATS host credentials bind a
  node to its own source subjects; application user/agent identity is still
  enforced. Peer input never becomes human input; credentials never enter model
  context or message bodies.
* Worker/control reservation partitions are removed. Transport routing and
  execution admission are separate concerns.

## What is proved and what remains

Actual authenticated NATS qualification discovers two service instances, stops
one and reads through the survivor, preserves original source identity, reports
no responders, and makes exactly one attempt on a lost reply. This is a transport
adapter qualification. Independent CPU/GPU instances are now also running as
additive private QA services. Standard Services discovery sees both authorities;
the restored combined HTTP catalog returns both sources in 621 ms. With the CPU
QA API and broker stopped, the GPU API cold-started in 1621 ms and served its own
room while reporting the CPU source unavailable. This is not a whole-host power
failure, cross-authority native execution, or NextComp phone qualification.

Before handover: actual independent CPU/GPU Hollywood instances; cross-authority
actor authorization/delegation; combined room/agent catalog through either entry;
real typed native request/reply; privacy, cancellation and original-operation
recovery. No implicit import of foreign principals, automatic room membership or
user impersonation is permitted to make a test pass.

## References

- [NATS Services API](https://beta-docs.nats.io/reference/2.14/services/)
- [NATS request/reply](https://docs.nats.io/concepts/request-reply)
- [Core NATS versus JetStream](https://github.com/nats-io/nats.docs/blob/master/using-nats/jetstream/develop_jetstream.md)
- [Transactional outbox](https://microservices.io/patterns/data/transactional-outbox.html)
- [Tailscale Services](https://tailscale.com/docs/features/tailscale-services)

Tailscale Services is a possible stable redundant client entrypoint, not a
replacement for Hollywood's catalog or authorization. Its tagged-host and
approval prerequisites must be verified before use; do not change live device
identity or Tailscale configuration merely to enable it.

## Runnable instance

The existing `hollywood-backbone` entrypoint accepts `--federation-config FILE`.
Without that flag it retains the existing API and lifecycle. With it, the same
API serves its local store and joins NATS Services; `GET /hollywood/v3/rooms`
assembles authorized catalogs from discovered authorities. Original entity IDs
and per-authority pagination remain intact. Source failures are explicit.

The private node configuration contains `authorityId`, credential-bound
`hostId`, TLS NATS `servers` seeds, owner-only `passwordFile`, `caFile`, and
optional `localSubjects`, `trustedViewers`, `knownAuthorities`. `localSubjects`
maps an already authenticated local principal to its global viewer subject.
`trustedViewers` maps that subject to an existing local principal. The destination
rechecks kind, active state and ordinary local authorization. An agent assertion
cannot become a human viewer; absent enrollment never grants admin access.
These mappings grant reads only, not human command or native execution authority.
Node credentials may publish only their own source subjects; discovery is not
permission to mutate another authority.

The actual two-store HTTP integration now verifies the combined rooms and agents
through either instance, source loss/reconnect, original authority IDs and
agent-to-human elevation denial. Physical independent-host catalog/outage qualification is now recorded privately.
Native cross-authority work delivery and the client product journey remain
separate qualification requirements.

`ops/build-node-zipapp.py` packages the reviewed wheel with Python's standard
zipapp tooling. Native dependencies remain in an existing pinned runtime; no new
packages or profiles are installed on the execution host. Packaged SQL uses
`importlib.resources`, so the same application works from a normal wheel or
zipapp. Build and dependency preparation remain on OZZZ-OS.

Node discovery and executor placement are separate: an API/catalog-only node
can join without provisioning a model worker. Agent mutations still require
the existing exact host/placement/native-source enrollment.

## Host-local Codex runtime boundary

Each execution host retains its own managed Codex execution runtime and
protected account profile. Hollywood API nodes and NATS discovery do not launch
models. Catalog reads, reconnects and peer transport messages must not create
another app-server or inference turn. Runtime/process identity belongs to the
existing execution lifecycle, not NATS membership.

The primary single-context connector currently launches a protected app-server
for an admitted delivery and closes it after settlement, while preserving the
agent's native thread. `capture_resident` belongs to the deferred purpose-context
runtime; it does not prove that the primary path is already resident. Transport
reads and discovery do not launch either process. Preserve the actual protected
execution lifecycle for federation qualification; any move to resident primary
processes needs its own implementation and proof. NextComp routes control to the
owning host and retains origin-qualified session IDs, receipts and drafts.

## Origin-qualified room reads

`GET /hollywood/v3/rooms/{id}` and its `/history` resource accept
`authority=<original-authority-id>` on a federated node. Omission preserves the
existing local API. Foreign reads use `hollywood.viewer.room.read` over NATS;
the destination resolves the explicitly enrolled viewer and checks ordinary
room/private-audience permissions. No bearer token crosses the bus. History
keeps its original event IDs, cursor, direction, limit and view. Unavailable
origins return an explicit error rather than another room or stale history.

This read-only mapping does not authorize agent mutation or impersonate a
foreign sender. Cross-node agent credential routing and native-source admission
are required before claiming the full local-node execution journey.

## Native agent through a local instance

An existing agent retains its original authority, identity, credential, context
and explicit execution placement. Its local HTTP connection includes
`X-Hollywood-Authority` and `X-Hollywood-Agent`. The local node forwards only
the credential digest over its own authenticated NATS source subject. The owning
store resolves the current exact host-agent binding; human credentials and
purpose-context credentials cannot enter this single-context route.

The existing HTTP handlers validate routes and schemas. Their common domain
invocation crosses NATS; the original store rechecks the credential/placement
inside its native-effect transaction. Exported operations are explicit; arbitrary
Store attributes and GET mutations are rejected. Native provenance is retained;
viewer mappings never authorize writes. Operation IDs and original wire data
are preserved. Replies are not retried after uncertainty. SSE uses the original
history cursors and rechecks authorization for every page. This retains the
existing bounded history stream cadence; NATS event-hint fanout is not claimed.

Host-local routing is not a foreign-principal import or automatic membership.
An existing agent's home authority remains its execution/receipt owner. Native
agents from independent authorities joining each other's rooms require explicit
federated membership/provenance integration; that separate path is not yet
qualified by the current transport tests.

## Host-owned identity candidate

Node-addressed subjects are `hollywood.v2.<authority>.node.<node>.service.<sourceHost>.<operation>`. Discovery retains `(authority,node)` rather than collapsing all nodes of an organization. Only one explicitly retained compatibility service may answer an old authority-only subject. Missing owners never fall through to another database.

`federation_identity` installs explicit room/agent references with no local bearer login or copied history. Current home revision, credential digest, source-host placement and receiving-room grants are rechecked in the canonical domain transaction. A role revision or revoked grant invalidates old admission.

`federation_home` is an operator-only drained enrollment primitive, not automatic agent migration. Persist one immutable plan, fence its original home, independently read back that exact fence, then accept it at the destination. A failure between steps leaves the login unavailable rather than active at two homes. Destination hosts and role references must already be explicitly enrolled. Retry returns the original receipt and does not undo a later revocation. Operators must reconcile current source state before accepting; an old saved receipt alone is not current authorization. Native contexts, account files, execution registrations, model processes and unfinished work are not copied or started.

Real PostgreSQL/NATS/HTTP qualification covers independent credentials, combined authorized rooms, private audience, source impersonation denial, stale-role rejection, fence-before-accept and retained foreign room references. This does not qualify the pending physical two-host native ownership journey.

Host controllers retain their existing HTTP capacity/executor routes. For a foreign controller on the local machine only, `federation_host` discovers the unique existing controller/host-policy binding and uses node-addressed NATS RPC. It exports only policy reads, capacity samples, executor lists and executor observations; each call rechecks active service credentials and the authenticated source host. It cannot post messages, act as an agent, start execution or observe another host. Ambiguous bindings fail closed. No new HTTP endpoint or SDK URL shim is introduced. Controller transport configuration must be explicitly moved to the local API; retired central routes are not revived.

Broker deployment must add v2 publish rules bound to each client's authenticated source UUID and v2 subscriptions bound to that client's own node UUID and explicitly served organizations. Validate and reload normal NATS configuration, then reconnect the owned API consumers; allowing a subject does not resurrect a previously rejected subscription. Never broaden source UUIDs to a wildcard.

Native preparation recovery distinguishes a claimed request with no executor/admission from an uncertain native launch. The existing recover-before-native API accepts an explicit administrator no-dispatch reconciliation digest for a stopped, unreserved preparation, with original revision/generation CAS and all canonical native/effect tables empty. This retains the failed preparation snapshot, re-admits the same request and exposes its old-claim lineage; it emits no false task result or parent model continuation. Unknown actual launches and cancellations retain their existing stricter checks. Transport routing uses the HTTP router's captured room/delivery/executor identity, including connector-prefixed context, briefing and admission routes.

#### Explicit host observation destinations (dev160)

`X-Hollywood-Host-Node` selects one separately enrolled work-owning node for
host policy reads, capacity samples and executor observations. It does not
select a different physical host: the listener fixes that identity, the caller
must be its current locally enrolled service controller, and the receiver
revalidates the same credential/controller under its transaction fence.
Agent and human credentials cannot use this controller transport. It cannot
start execution, send messages, or perform administrative operations.

SDK206 uses separate durable journals and independent bounded I/O lanes per
reviewed observation node. Physical QA now demonstrates local GPU native work
with the CPU QA API and broker absent. This does not imply global atomic
multi-node capacity reservation, storage failover, or a finished human client.
