# Human context seed drafts

This is a canonical-content review API. It does not approve or install a new
native window, start inference, change the agent's runtime selections, or
certify semantic continuity. The complete LAX requirement remains in
`losangelex/requirements/manual-context-windows-20261025.json` in the LAX fork.

The first slice requires an authenticated, active **human administrator** in
the target agent's authority. A draft belongs to that human reviewer. Agent
administrators, services and purpose credentials cannot impersonate the reviewer.
Nonadministrator review grants and shared review are not yet implemented.
Room/event and purpose visibility must also hold for the target agent.

Under `/hollywood/v3/agents/{agent}/contexts/{reader|writer|responder}`:

- `GET /seed-sources?reference=role` reads an eligible current canonical record,
  revision and SHA256. Other references use existing purpose retrieval:
  material UUIDs, `goal:UUID`, `work:UUID`, and reader-only `event:UUID`.
  Native `exec:` whole-item retention requires its separate protocol.
- `POST /seed-drafts` takes `{id, operationId}` with a client-persisted draft UUID
  and original operation ID. It captures agent/context/thread/generation and
  pins the current canonical role. It does not copy the runtime's relevance
  marks into a second authoritative selection.
- `PUT /seed-drafts/{draft}/marks` takes `operationId`, current draft `revision`,
  `reference`, exact `sourceRevision` and `sourceSha256`, strict boolean `keep`,
  and `reason`. Each successful change increments the draft revision.
  Human marks retain reviewer identity and authorship. Removing a mark preserves
  its original source; a role pin cannot be removed. Re-marking the current role
  after an authorized canonical edit is an explicit review action.
- `GET /seed-drafts/{draft}` reauthorizes sources and returns one repeatable-read
  canonical snapshot with mandatory current role, selected source records,
  current active purpose material and an unfinished-obligation index. Changes
  to pinned content or context generation produce
  `CONTEXT_SEED_REVIEW_REQUIRED`; the server does not silently follow a newer pin.
- `GET /seed-drafts/{draft}/operations/{operation}` reads the original
  metadata-only receipt after an uncertain response. A missing committed receipt
  returns `CONTEXT_OPERATION_NOT_FOUND`. Reusing an operation with changed
  content conflicts. A receipt does not install a window or admit work.

Example mark body (IDs/hashes are obtained from the source and draft reads):

```json
{
  "operationId": "review-pin-1",
  "revision": 1,
  "reference": "event:00000000-0000-4000-8000-000000000001",
  "sourceRevision": 1,
  "sourceSha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "keep": true,
  "reason": "New fact learned during the closing window"
}
```

`size.canonicalBytes` is the exact encoded canonical-content size.
`assembledNativeBytes`, `estimatedTokens` and `authoritativeInputTokens` are
explicitly null: native instructions, tools and the final provider input have
not yet been assembled. The response is `stage=canonical-material-preview`
and `nativeInputReady=false`. It is not the final reviewed seed promised by the
complete workflow. The current canonical 98,304-byte and 16 optional-mark limits
remain enforced; oversize preview refuses without dropping user pins.

Role/goal/plan edits continue through their existing authorized, versioned
canonical APIs. This preview creates no alternate role, goal or plan.
Reviewed freeze/commit, native preparation and full seed metrics, whole-window
choice, real-model continuation, and NextComp controls are required next slices.
There is no production default change in this release candidate.

Qualification: `tests/integration_context_seed_review.py` creates an owned
disposable PostgreSQL schema, binds only loopback HTTP, and retains private
receipts. It uses explicitly declared fixture messages and enrollment metadata.
It starts no workers/models and does not prove semantic or physical-phone
behavior. Use a fresh evidence directory; inspect an existing run instead of
replaying its setup.
