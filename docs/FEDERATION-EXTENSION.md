# Hollywood federation: extend the transport

Status: candidate, not yet a deployed multi-host product.

Hollywood remains the application. Each instance keeps its existing API, store,
room authority, operation receipts and native agent lifecycle. Add one NATS
connection and a thin NATS Services adapter; do not replace the database or invent
a distributed scheduler. Independent stores have independent authority IDs.

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
adapter qualification on one host, not physical/native/NextComp product proof.

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
agent-to-human elevation denial. Physical independent-host and native
cross-authority work delivery remain separate qualification requirements.

`ops/build-node-zipapp.py` packages the reviewed wheel with Python's standard
zipapp tooling. Native dependencies remain in an existing pinned runtime; no new
packages or profiles are installed on the execution host. Packaged SQL uses
`importlib.resources`, so the same application works from a normal wheel or
zipapp. Build and dependency preparation remain on OZZZ-OS.

Node discovery and executor placement are separate: an API/catalog-only node
can join without provisioning a model worker. Agent mutations still require
the existing exact host/placement/native-source enrollment.
