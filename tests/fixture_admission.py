"""Explicit capacity fixtures for legacy HTTP contracts; never native qualification."""

import hashlib
import json
import secrets
from uuid import uuid4

CAPABILITIES = [
    "native-role-goals/v1",
    "cooperation-plan/v1",
    "work-attempts/v1",
    "room-briefing/v1",
    "host-admission/v1",
    "portfolio-supervision/v1",
]


def prepare(store, owner_id, host_id, root):
    path = root / ("capacity-fixture-" + host_id + ".json")
    if path.exists():
        intent = json.loads(path.read_text())
    else:
        intent = {
            "controllerId": str(uuid4()),
            "token": secrets.token_urlsafe(40),
            "policyOperation": "fixture-policy:" + str(uuid4()),
            "sampleOperation": "fixture-sample:" + str(uuid4()),
            "machineIdentity": hashlib.sha256(("fixture:" + host_id).encode()).hexdigest(),
        }
        path.write_text(json.dumps(intent))
    store.principal(
        owner_id,
        intent["controllerId"],
        "fixture-controller-" + host_id[:12],
        "service",
        intent["token"],
    )
    current = store.host_policy(owner_id, host_id)["policy"]
    if current is None:
        with store.pool.connection() as c:
            owner = store.actor(c, owner_id)
            controls = c.execute(
                "SELECT id FROM hb_principal WHERE authority=%s AND kind='agent' AND administrator",
                (owner["authority"],),
            ).fetchall()
        store.host_policy_set(
            owner_id,
            host_id,
            intent["policyOperation"],
            {
                "revision": 0,
                "controllerId": intent["controllerId"],
                "machineIdentity": intent["machineIdentity"],
                "maxConcurrent": 64,
                "reservedInteractive": 1,
                "controlAgentIds": [str(a["id"]) for a in controls],
                "memoryPerTurnBytes": 1024**3,
                "reserveMemoryBytes": 0,
                "sampleMaxAgeSeconds": 300,
                "connectorMaxAgeSeconds": 300,
            },
        )
        store.host_sample(
            intent["controllerId"],
            host_id,
            intent["sampleOperation"],
            {
                "machineIdentity": intent["machineIdentity"],
                "cpuSlots": 64,
                "availableMemoryBytes": 128 * 1024**3,
            },
        )
