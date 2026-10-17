# Ordinary delivery editing and agent resource access

An admitted ordinary delivery can edit a registered physical resource without a
Goal, Work or coedit plan. The server derives the scope from the canonical request:
if its data names structured work, the existing planned/consented route remains
mandatory. Neither the model nor a callback field can select a weaker scope.

The exact versioned wire contract is native-delivery-edit-wire-r01.json and the
request schemas are delivery-edit.schema.json and agent-grant.schema.json.
Endpoints are relative to /hollywood/v3:

- POST collaboration/delivery-edits: preflight, begin, settle.
- GET collaboration/delivery-edits/{permit}: original read-only permit evidence.
- GET collaboration/delivery-targets/{delivery}: controller-scoped resource data.
- PUT physical-workspaces/{workspace}/agent-grants/{agent}: administrator CAS grant.

No grant means no access. Schema29 permits grant metadata setup for an enrolled paused
placement; applying still requires enabled placement and live original source.
No context or work is needed to grant a newly provisioned principal.
Revocation remains possible after a placement or principal is disabled. Existing
root registration and actual OS permissions stay separate. Changing this canonical
data never rewrites bridge enrollment or requires unrelated channel restarts.

Mutation uses the current protected native source v2 and attached live executor,
exact delivery/generation/claim/role/context/thread/turn, decisions, room rights,
root identity/environment and per-operation resolved path attestations. Current
unsettled controls, cancellation, changed/revoked authority and competing original
participations remain fences. The server validates canonical facts and trusts the
registered controller for actual callback, root-handle and byte observations; it
cannot turn an arbitrary model path or digest into filesystem authority.

Both permit scopes use hb_edit_permit and hb_edit_file_lock. Their namespace
advisory key is identical to legacy path ownership: paths: plus the digest of
[authority, physical host, physical workspace]. Unreleased legacy file/directory
ownership, including expired held claims, cannot be bypassed by ordinary scope.
Existing planned participants and explicit consent semantics remain intact.

Preflight binds exact before/after files and the actual patch. A non-Git source
uses explicit null sourceRevision. Only the original successful begin response
contains an apply admission. Durable receipts always say mayApply=false; replay
returns no admission. Conclusive applied/notApplied settlement releases only the
original direct permit locks. Unknown, partial, expired or revoked effects remain
held. Independently stopped observations are evidence only; they do not release
held locks or create a new execution. Later reviewed reconciliation of such held
ordinary effects remains a separate explicit gate.

Schema27 preserves old plan permits with a default plan discriminator, makes
plan fields nullable only for delivery permits through a strict shape constraint,
and adds revisioned principal resource grants. It does not rewrite old receipts,
claims, cancellation or context rows. Protocol records remain filtered audit and
native activity, rather than conversation messages or fabricated task lifecycle.

The finite delivery-edits PostgreSQL/HTTP harness uses explicitly SYNTHETIC native
provenance. Ordinary agents have zero work rows; an existing planned fixture is
used only for coexistence/conflict checks. This qualification is not native runtime
or installed end-state acceptance. LAX owns native integration and its source/index;
root owns independent installed model qualification, cutover and rollback.
