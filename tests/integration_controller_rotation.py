"""Actual PostgreSQL host-lock contention and controller-rotation authorization."""

import concurrent.futures
import json
import os
import secrets
import time
from pathlib import Path
from uuid import uuid4

from hollywood_backbone.admission import lock
from hollywood_backbone.store import Store, Denied, json_value


def main():
    os.umask(0o077)
    root = Path(os.environ["ROTATION_EVIDENCE"])
    root.mkdir(mode=0o700)
    original = Path(os.environ["ROTATION_ORIGINAL"])
    enroll = json.loads((original / "host-enrollment.json").read_text())
    config = json.loads(Path(enroll["configPath"]).read_text())
    setup = json.loads((original / "enterprise/installation-intent.json").read_text())
    owner = setup["credentials"]["Owner"]["principalId"]
    host = enroll["hostId"]
    store = Store(json.loads(Path(os.environ["ROTATION_DATABASE_CONFIG"]).read_text()))
    principal = {"principalId": str(uuid4()), "token": secrets.token_urlsafe(40)}
    (root / "new-controller-intent.json").write_text(json.dumps(principal, indent=2))
    store.principal(
        owner, principal["principalId"], "Owned rotated controller", "service", principal["token"]
    )
    policy = store.host_policy(owner, host)
    previous = policy["policy"]
    with store.pool.connection() as connection:
        row = connection.execute("SELECT * FROM hb_executor WHERE host=%s", (host,)).fetchone()
    assert row and row["state"] == "unknown" and row["launch_phase"] == "reserved"
    old = json_value(row)
    payload = {
        "revision": previous["revision"],
        "controllerId": principal["principalId"],
        "machineIdentity": previous["machine_identity"],
        "maxConcurrent": previous["max_concurrent"],
        "reservedInteractive": previous["reserved_interactive"],
        "controlAgentIds": policy["controlAgentIds"],
        "memoryPerTurnBytes": previous["memory_per_turn"],
        "reserveMemoryBytes": previous["memory_reserve"],
        "sampleMaxAgeSeconds": previous["sample_max_age"],
        "connectorMaxAgeSeconds": previous["connector_max_age"],
    }
    observation = {
        "operationId": "revoked-waiter:" + str(uuid4()),
        "machineIdentity": config["machineIdentity"],
        "descriptorDigest": old["descriptor_digest"],
        "state": "unknown",
        "evidence": old["evidence"],
    }
    (root / "rotation-intent.json").write_text(
        json.dumps(
            {
                "hostId": host,
                "executorId": old["id"],
                "configure": payload,
                "observation": observation,
            },
            indent=2,
        )
    )

    with store.pool.connection() as blocker:
        lock(blocker, host)
        lock_row = blocker.execute(
            "SELECT classid,objid,objsubid FROM pg_locks WHERE pid=pg_backend_pid() AND locktype='advisory' AND granted"
        ).fetchone()
        assert lock_row

        def waiters(number):
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                with store.pool.connection() as connection:
                    count = connection.execute(
                        "SELECT count(*) AS n FROM pg_locks WHERE locktype='advisory' AND NOT granted AND classid=%s AND objid=%s AND objsubid=%s",
                        (lock_row["classid"], lock_row["objid"], lock_row["objsubid"]),
                    ).fetchone()["n"]
                if count >= number:
                    return
                time.sleep(0.05)
            raise RuntimeError("Actual host-lock waiter did not appear")

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            try:
                changed = pool.submit(
                    store.host_policy_set, owner, host, "rotation-config:" + str(uuid4()), payload
                )
                waiters(1)
                old_waiter = pool.submit(
                    store.executor_observe,
                    config["tokenPrincipalId"]
                    if "tokenPrincipalId" in config
                    else enroll["controllerId"],
                    host,
                    old["id"],
                    observation,
                )
                waiters(2)
                blocker.commit()
                assert changed.result(timeout=20)["accepted"]
                try:
                    old_waiter.result(timeout=20)
                except Denied:
                    denied = True
                else:
                    raise AssertionError("Revoked controller retained authorization after waiting")
            finally:
                blocker.rollback()

    with store.pool.connection() as connection:
        after = json_value(
            connection.execute("SELECT * FROM hb_executor WHERE id=%s", (old["id"],)).fetchone()
        )
        delivery = connection.execute(
            "SELECT state FROM hb_delivery WHERE id=%s", (old["delivery"],)
        ).fetchone()
    assert after == old and delivery["state"] == "unknown"
    result = {
        "passed": True,
        "actualPostgresLockWaiters": 2,
        "controllerRotationCommittedBeforeOldWaiter": True,
        "revokedWaitingControllerDenied": denied,
        "originalExecutorRecordUnchanged": True,
        "originalDeliveryRemainsUnknown": True,
        "metadataOnly": True,
        "providerTurns": 0,
        "productionChanged": False,
    }
    (root / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    store.close()
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
