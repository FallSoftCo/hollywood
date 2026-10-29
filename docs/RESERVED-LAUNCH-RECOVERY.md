# Explicit recovery before native process dispatch

The existing `deliveries/{id}/recover-before-native` operation retains its legacy
settled-failure path. An administrator may additionally reconcile an `unknown`
reserved launch using `executorId`, `outcome: native_launch_not_dispatched` and a
`reconciliationSha256` bound to the inspected original durable launch journals.
The original revision/generation and explicit null `resultEventId` are required.

This is an explicit administrator attestation, never automatic inference from
an absent PID. The canonical executor must be the only original executor, still
reserved, independently stopped by the current host controller, with no native
PID/turn or later execution/activity/effect/workflow evidence. Cancellation blocks
recovery. An attached or uncertain executor cannot use this path.

The original request/event and executor remain intact. A recovery audit stores
the original delivery, executor observation and attestation digest. Normal claim
admission advances generation; no original request is resubmitted. Repeating an
accepted operation returns its original administrative receipt only.

This path must not be used when dispatch or an external action is uncertain.
