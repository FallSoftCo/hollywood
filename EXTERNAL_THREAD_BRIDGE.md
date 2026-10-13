# External thread execution bridge

Current verified support is metadata discovery and reading. Association is a separate
product operation; it must not enable execution merely by creating a database row.
The proposed MCP bridge below is not yet runtime-verified or implemented.

## Identity and authority

An external reference is `(registered host, canonical CODEX_HOME, thread UUID)`.
Keep the host's configured app-server endpoint and its authentication separately.
Paths and endpoints must come from owner-approved registrations, not model arguments.
Do not copy a rollout into another home to acquire ownership: writer locks are scoped
to the original home, and copying produces independent writers with the same UUID.

The existing Room bearer identifies the environment owner. Agent-visible associations
must additionally carry their team, task, agent and history visibility. A private
external conversation must not become public through its title, preview, cwd, tool
output or imported history. Store a reference instead of copying transcript rows into
the room. Metadata access and history access should remain distinct capabilities.

## Writer handoff

1. Discover with `thread/list` and read metadata with `thread/read` without turns.
   `useStateDbOnly=true` avoids scan-and-repair, but can omit unindexed rollouts.
   Explicit source/provider/archive filters are necessary for complete enumeration.
2. Associate the reference as an observer. Record `execution=false` and an explicit
   compatibility/ownership reason until the remaining steps succeed.
3. Reserve a durable owner lease using compare-and-swap, keyed by the external
   reference. Persist the owner, operation ID, state and monotonically increasing
   generation before performing RPCs. Competing controllers must fail the reservation.
4. For an existing compatible thread on the same owning app-server, connect to that
   endpoint and rejoin. Do not start another process to take its writer. For a cold
   handoff, require the previous owner to finish/interruption-confirm its turn and
   release execution. `thread/unsubscribe` is not release acknowledgment: turns may
   continue and unloading is delayed. `notLoaded` is only server-local knowledge.
5. Resume the original UUID against the original home. The runtime's OS writer lock
   is the final same-home arbiter. An active-writer error leaves the association in
   observer/conflict state. Never remove lock files, kill an unrelated app-server,
   or create a substitute thread on conflict.
6. Confirm the returned UUID and tool compatibility before enabling turn dispatch.
   Fence every later dispatch and bridge-tool call with the lease generation.
   An uncertain resume must be reconciled against the same owner endpoint before
   retrying; it must not create another thread or owner.

An expired database lease does not prove the old runtime stopped. Do not steal a
writer on timeout alone. Cross-host ownership requires one authority for this lease;
separate Room databases and filesystem locks do not provide distributed fencing.

## Tool compatibility and proposed MCP adapter

The runtime restores dynamic tools from persisted session metadata. The installed
`ThreadResumeParams` schema has no `dynamicTools` replacement field. A generic CLI
thread therefore cannot acquire Hollywood's dynamic tools by ordinary resume.

The candidate bridge is a host-local MCP adapter supplied through a cold resume's
`config.mcp_servers` override. It exposes the existing Hollywood tool contracts using
MCP tool names and dispatches through the same authorization/validation code as native
Hollywood tools. It must not emulate tools by reading generated assistant prose.
Existing saved dynamic tools remain unchanged; unsupported client-owned tools must
be explicitly handled or block execution adoption.

The adapter receives an opaque capability through a protected local channel or file.
Its server-side binding fixes owner, team, task, agent, thread and lease generation;
model-supplied IDs never override that authority. Revocation and generation mismatch
reject calls. Avoid credentials in model-visible configuration, command arguments,
history or logs. Preserve the external thread's original permission policy and
instructions unless the owner explicitly authorizes a change. Do not silently apply
Hollywood's full-access defaults or replace saved instructions during adoption.

This path needs a disposable runtime proof: Hollywood currently starts app-server
with `mcp_servers={}`. Verify that the chosen resume override actually creates the
adapter connection and makes its tools callable. If it does not, implement a native
runtime extension with explicit compatibility semantics instead of modifying JSONL
or claiming adoption worked. Same-server hot resume may also reject config changes;
it must not silently fall back to a fresh thread.

## Preservation and verification gates

Before a fixture handoff, record the UUID, metadata, history and rollout bytes. After
resume and one controlled turn, verify the original history prefix is unchanged and
only the expected new turn was appended. Cover legacy and paginated history without
assuming JSONL is the only authoritative storage. Never restore an old database over
newly accepted live work as an automatic rollback.

Required independent checks:

- Metadata list/read and observer association issue no resume/start/turn RPC.
- Owner, team, home and endpoint mismatches fail; private data does not cross scopes.
- Two concurrent owners yield one lease/writer; a stale generation cannot call tools.
- A live writer rejects takeover; unsubscribe during a turn does not grant ownership.
- Compatible same-server rejoin retains UUID/history; cold same-home resume succeeds
  only after the original owner releases it.
- Generic CLI history remains intact and gains callable bridge tools only after an
  actual MCP connection/tool-call proof; unknown saved tools are reported honestly.
- RPC timeout, process restart and duplicate command IDs preserve the same pending
  operation and never create duplicate threads or repeat uncertain turns.

Source evidence in `losangelex-next/codex-rs`: `rollout/src/writer_lock.rs`,
`app-server/src/request_processors/thread_processor.rs`,
`app-server-protocol/src/protocol/v2/thread.rs`, and `core/src/session/mod.rs`.
Installed local runtime inspected: Codex 0.154.0. This document defines a design and
verification gate, not a claim that external execution adoption is available.
