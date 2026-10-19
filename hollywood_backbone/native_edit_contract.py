"""Document actual native admission semantics; this is not an effect receipt."""

def contract():
    return {"schema":"lax-native-edit-admission/v1",
        "nativeSource":"codex-rs/core/src/canonical_patch.rs:PatchPermit.reserve",
        "preDispatchRejections":{
            "exactDiagnostic":"Canonical patch held: protected patch-preflight port unavailable; inspect/reconcile the original operation before another mutation",
            "phase":"before_bridge_dispatch","permitCreated":False,"filesystemMutationStarted":False,
            "recovery":"For this exact original native diagnostic, no permit exists to reconcile. Verify the current protected channel and original target, then submit a fresh native edit under current authority. Never bypass native admission with another write path."},
        "unknownAfterDispatch":"A timeout, lost bridge reply, begun mutation or unknown settlement retains the original operation and locks until its actual receipts/effects are reconciled.",
        "meaning":"Native implementation contract, not confirmation that any particular historical operation matched it."}
