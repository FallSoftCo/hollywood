"""Canonical data captured inside an already authenticated inference admission.

The existing context operation is the receipt; availability is never comprehension.
No room message, execution, permission or additional task is created.
"""
from . import context_material
from .store import Conflict

MAX_BYTES = 196608
MAX_FRAGMENTS = 64


def prepare(c, store, actor, purpose, binding, p, ids):
    packet = {"schema": "lax-purpose-primary-input/v1",
        "contextId": str(binding["context_id"]), "purpose": purpose,
        "nativeThread": p["nativeThread"], "nativeTurn": p["nativeTurn"],
        "materialIds": list(ids), "snapshots": [],
        "meaning": "Exact purpose data at admission; availability is not comprehension, "
                   "completion or additional authority. Current contribution checks remain required."}
    if purpose == "reader":
        # ReaderInbox already injects original attributable input. The SDK must
        # adopt that exact inbox before acknowledging the native admission gate.
        return {**packet, "mode": "reader_inbox"}
    reads = dict(binding["reads"])
    snapshots = []
    for reference in ["role", *ids]:
        snapshot = context_material.complete(c, store, actor, purpose,
            {**binding, "reads": reads}, p["nativeTurn"], {"reference": reference})
        reads[reference] = {"revision": snapshot["revision"], "turn": p["nativeTurn"]}
        snapshots.append(snapshot)
    if (sum(s["totalBytes"] for s in snapshots) > MAX_BYTES
            or sum(len(s["chunks"]) for s in snapshots) > MAX_FRAGMENTS):
        raise Conflict("Complete primary input exceeds admission budget; no inference admitted")
    return {**packet, "mode": "material_snapshots", "snapshots": snapshots}
