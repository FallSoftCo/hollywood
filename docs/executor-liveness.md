# Executor liveness and unresolved effects

Schema 15 separates a native executor incarnation from its delivery. An executor
captures the exact authority/agent/host, original claim/generation, machine/boot,
systemd invocation/control group and resident/native process start ticks.

Only the active owning connector can capture an originally claimed delivery.
Changed capture contents conflict. An enrolled host-controller service, rather
than the executing agent, records independent live/unknown/stopped observations.
The controller checks administrator-protected service enrollment against actual
Linux process, executable inode/owner and systemd evidence. Its credentials remain
in the protected host registry. Ordinary agents cannot attest another fact source.

A stopped resource is immutable. Its acceptance never means external effects were
undone, an unknown delivery failed without effects, business work was accepted or
the old operation may be sent again. A matching original delivery still marked
claimed/running/cancel-requested becomes unknown on conclusive executor stop.
Obsolete pending native prompts expire; submitted consumption remains unknown.

Admission counts a captured/live/unknown executor as physical occupancy. A
conclusively stopped executor releases that occupancy while the semantic delivery
stays unknown. Old active deliveries without executor evidence remain conservative.
The agent's semantic hold remains until the native control/continuation path is
completed. Host capacity and business readiness are different decisions.

Routes:
- POST /hollywood/v3/connector/deliveries/{delivery}/executor
- GET /hollywood/v3/executors/{executor}
- GET /hollywood/v3/hosts/{host}/executors?after={uuid}&limit={1..256}
- POST /hollywood/v3/hosts/{host}/executors/{executor}/observation

The listing uses stable UUID pages; the controller coalesces unchanged facts and
retains the same operation after a lost acknowledgement. Room progress states
executor stop and preserved uncertainty without exposing raw process/credential
details.

Current qualification is Linux/systemd on one host. The API/controller foundation
does not yet instrument Connector.once(), admit an original-Global inspection
turn, apply a native tool ceiling or fence late parent-generation effects. Those
remain explicit parts of the same implementation slice and release gates. No
production or phone cutover is implied.
