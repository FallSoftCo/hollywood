# Authenticated reviewed native windows (candidate)

Hollywood owns the human review and its durable source/approval records. LAX owns
native preparation, installation and independent file verification. No additional
coordinator or service is introduced. This is candidate H124/schema40 + SDK168,
not a statement that the live installation has switched.

The initial control scope is an authenticated human administrator's own draft
and an explicitly paired **idle Writer**. Installation/upgrade accepts
`manualSeedControl: 1` only with the explicit retained Writer policy. Native
binding negotiates the version under a distinct receipt. Default absence is off.
Existing reader/responder profiles do not acquire this capability.

After using the draft/source/mark/preview API, operate under
`/hollywood/v3/agents/{agent}/contexts/writer/seed-drafts/{draft}`:

- `POST /control`, `action=prepare`, takes the draft revision and exact
  `canonicalSha256`. It freezes the selected canonical records and captures
  reviewer provenance. It refuses active inference, capacity reservations,
  incompatible profiles and competing reviews/rebuilds. It blocks only new
  inference for this purpose and consumes no inference permit.
- `GET /control` reads control state independently of pin freshness, including
  when cancellation is needed. `GET /control/content` returns frozen canonical
  content under current source authorization. Installed, cancelled and superseded
  snapshots are explicitly historical audit artifacts; they are not current policy.
- The existing worker's metadata lane prepares the native packet asynchronously
  and reports its exact preflight, sealed approval digest and runtime identity.
  No room message or model steering is used for this control.
- `POST /control`, `action=approve`, takes the current control revision and
  exact native `approvalDigest`. Only the original authenticated human can
  approve their draft. The purpose then rechecks the human credential/authority,
  source versions, native identity and approved preflight before dispatch.
- `action=cancel` records intent. Admission stays blocked until the host confirms
  known no-dispatch cancellation. Applying or uncertain native operations cannot
  be cleared through this path. Known unsubmitted preparation failures remain
  cancellable; original native journals and H receipts stay retained.
- To change a frozen review, create/mark a new draft under the same agent and
  purpose, then send `action=revise` with current control revision,
  `replacementDraftId` and the replacement's `canonicalSha256`. The host cancels
  only the old unsubmitted preparation. Hollywood then atomically transfers the
  admission hold to the newly frozen draft; it never opens an execution gap.
  Review and approve the replacement's own prepared digest.
- Native installation uses the original snapshot/operation/preflight and receipt.
  It increments the same context generation, preserves queued input and does not
  complete Work or copy human pins into the model's mutable relevance selection.

Every control write requires `operationId`; human receipts use the existing
draft operation read route. Purpose receipts use the existing context operation
route. Accepted, locally dispatched, natively installed and metadata-recorded
facts remain distinct. Unknown native outcomes require original-receipt
reconciliation; no automatic replacement replay is authorized.

Control revisions are distinct from draft revisions. Each draft retains one
immutable review lifecycle. Source/role/goal/plan changes occur through their
canonical APIs; replacement reviews retain old source/version/hash provenance.

States include requested, prepared, approved, applying, installed,
cancel_requested, cancelled, refused, revise_requested and superseded.
Unsubmitted refusal is not native execution failure. A no-dispatch observation
after H admission must match the original native cut/history; otherwise the
outcome stays held. Waiting for human review holds no native callback open.
The asynchronous worker lane must finish observing its original task before
ordinary inference can resume.

To disable, drain/settle reviews and use the explicit SDK168
`manualSeedControl: 0` configuration/bind transition. Do not roll old SDK/H code
over an unsettled review. Code/config rollback preserves canonical databases,
native history, original work and receipts; it does not restore a database backup.

Qualification uses actual authenticated HTTP, PostgreSQL, SDK, native JSON-RPC,
recorded checkpoints and WebSocket provider requests. Provider completions and
initial material are declared fixtures. The joined journey checks two native
generations, source revision/hold transfer, queued Reader input, approval,
cancellation, pre-dispatch failure recovery and explicit disable. This is not
real-model semantics or an actual full-agent enterprise journey.

Remaining required gates: frozen actual-model same-Work continuation in the real
purpose runtime, active-turn quiescing, all three purpose windows, nonadmin/shared
review, whole-window/native-action human selection, manual hard-limit behavior,
full assembled provider input/token accounting and NextComp controls.
`fullProviderInputReady=false` is intentional: canonical/native replacement
bytes do not measure all tool/instruction request overhead or actual provider tokens.
