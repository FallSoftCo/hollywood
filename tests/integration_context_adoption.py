"""Real PostgreSQL existing-agent transition, including competing legacy claim.

Run only on an explicitly designated disposable database copy. Fixtures roll
back; the final concurrency case commits the copied agent's metadata enrollment.
No filesystem installer, model, worker or live user operation is invoked.
"""
import argparse
import contextlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

from psycopg.types.json import Jsonb

from hollywood_backbone import admission
from hollywood_backbone.context_system import configure_transaction
from hollywood_backbone.store import Conflict, Store, digest


def main(args):
    if args.output.exists():
        raise ValueError("Retain original result; choose a new explicit trial")
    config = json.loads(args.database_config.read_text())
    store = Store(config)
    checks = {}
    report = {"schema": "hollywood-native-adoption-pg/v1", "modelsStarted": 0,
              "servicesStarted": 0, "scope": __doc__, "checks": checks}

    def mark(name, value):
        checks[name] = bool(value)
        assert value, name

    try:
        with store.pool.connection() as c:
            database = c.execute("SELECT current_database() AS name").fetchone()["name"]
            if database != args.disposable_database:
                raise ValueError("Explicit disposable database differs")
            actor = store.actor(c, args.agent_id)
            store.admin(actor)
            placement = c.execute("SELECT * FROM hb_placement WHERE agent=%s", (args.agent_id,)).fetchone()
            legacy = c.execute("SELECT * FROM hb_agent_context WHERE principal=%s", (args.agent_id,)).fetchone()
            assert legacy and placement and placement["enabled"]
            assert not c.execute("SELECT 1 FROM hb_context_system WHERE agent=%s", (args.agent_id,)).fetchone()
            assert not c.execute("""SELECT 1 FROM hb_delivery WHERE agent=%s
                AND state IN ('claimed','running','unknown','cancel_requested','pending')""",
                (args.agent_id,)).fetchone()
            token_count = c.execute("SELECT count(*) AS n FROM hb_token").fetchone()["n"]
            payload = {"hostId": str(placement["host"]), "workspaceId": str(uuid4()),
                       "bindings": [{"purpose": p, "contextId": str(uuid4()),
                                     "credentialDigest": digest(str(uuid4()))}
                                    for p in ("reader", "writer", "responder")]}
            role = c.execute("SELECT * FROM hb_agent_role WHERE principal=%s", (args.agent_id,)).fetchone()

            def fixture(state="pending", **changes):
                event = store.event(c, actor, str(role["home_room"]), "request",
                    "COPY ONLY native-adoption substrate fixture; no execution",
                    recipient=args.agent_id, data={"fixture": "native-adoption-pg"})
                row = {"state": state, "generation": 0, "claim_id": None,
                       "native_thread": None, "native_turn": None,
                       "cancellation_requested": False, **changes}
                identity = str(uuid4())
                c.execute("""INSERT INTO hb_delivery
                    (id,authority,event,agent,host,state,generation,claim_id,native_thread,native_turn,cancellation_requested)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (identity, actor["authority"], event, args.agent_id, placement["host"],
                     *(row[k] for k in ("state", "generation", "claim_id", "native_thread",
                                        "native_turn", "cancellation_requested"))))
                return identity, event

            def refused(name):
                try:
                    configure_transaction(store, c, actor, args.agent_id, payload)
                except Conflict:
                    mark(name, not c.execute("SELECT 1 FROM hb_context_system WHERE agent=%s",
                         (args.agent_id,)).fetchone()
                         and c.execute("SELECT count(*) AS n FROM hb_token").fetchone()["n"] == token_count)
                else:
                    raise AssertionError(name + ": unsafe transition accepted")

            for state in ("claimed", "running", "cancel_requested", "unknown"):
                with c.transaction(force_rollback=True):
                    fixture(state, generation=1, claim_id="copy-only-legacy")
                    refused("reject-unsettled-" + state)
            variants = {"generation": {"generation": 1},
                        "claim": {"claim_id": "copy-only-legacy"},
                        "thread": {"native_thread": "copy-only-thread"},
                        "turn": {"native_turn": "copy-only-turn"},
                        "cancel": {"cancellation_requested": True}}
            for name, values in variants.items():
                with c.transaction(force_rollback=True):
                    fixture(**values)
                    refused("reject-tainted-pending-" + name)
            for state in ("captured", "live", "unknown"):
                with c.transaction(force_rollback=True):
                    delivery, _ = fixture("completed", generation=1, claim_id="copy-only-legacy")
                    c.execute("""INSERT INTO hb_executor
                        (id,authority,delivery,agent,host,generation,claim_id,descriptor,descriptor_digest,state)
                        VALUES(%s,%s,%s,%s,%s,1,%s,%s,%s,%s)""",
                        (uuid4(), actor["authority"], delivery, args.agent_id, placement["host"],
                         "copy-only-legacy", Jsonb({"fixture": "not-a-real-process"}), "fixture", state))
                    refused("reject-undrained-terminal-executor-" + state)
            with c.transaction(force_rollback=True):
                delivery, event = fixture()
                result = configure_transaction(store, c, actor, args.agent_id, payload)
                mark("clean-original-pending-ingested", result["existingPendingInputsIngested"] == 1
                     and str(c.execute("SELECT source FROM hb_context_material WHERE agent=%s AND kind='input'",
                                      (args.agent_id,)).fetchone()["source"]) == str(event))
                again = configure_transaction(store, c, actor, args.agent_id, payload)
                mark("same-enrollment-reconciles", again["reconciled"]
                     and c.execute("SELECT count(*) AS n FROM hb_token").fetchone()["n"] == token_count + 3)
                mark("legacy-history-retained", c.execute("SELECT * FROM hb_agent_context WHERE principal=%s",
                                                        (args.agent_id,)).fetchone() == legacy)
            mark("rollback-fixtures-removed", not c.execute(
                "SELECT 1 FROM hb_context_system WHERE agent=%s", (args.agent_id,)).fetchone()
                and c.execute("SELECT count(*) AS n FROM hb_token").fetchone()["n"] == token_count)

        # This metadata-only commit is restricted to the named database copy.
        # Hold enrollment open while the actual public legacy claimant starts.
        claimant = Store(config)
        started = threading.Event()
        state = {}
        original_pool = claimant.pool
        class InstrumentedPool:
            @contextlib.contextmanager
            def connection(self):
                with original_pool.connection() as connection:
                    connection.execute("SET LOCAL statement_timeout='8s'")
                    state["pid"] = connection.execute("SELECT pg_backend_pid() AS pid").fetchone()["pid"]
                    started.set()
                    yield connection
        claimant.pool = InstrumentedPool()
        def old_claim():
            try:
                value = claimant.claim(args.agent_id, "copy-native-adoption:" + str(uuid4()),
                                       payload["hostId"], sorted(admission.CAPABILITIES))
                return {"result": value}
            except Conflict as error:
                return {"conflict": str(error)}
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                with store.pool.connection() as c:
                    actor = store.actor(c, args.agent_id)
                    enrollment = configure_transaction(store, c, actor, args.agent_id, payload)
                    owner_pid = c.execute("SELECT pg_backend_pid() AS pid").fetchone()["pid"]
                    future = executor.submit(old_claim)
                    assert started.wait(3), "legacy claimant did not start"
                    deadline = time.monotonic() + 5
                    blocked = None
                    while time.monotonic() < deadline:
                        blocked = c.execute("""SELECT query,wait_event_type,pg_blocking_pids(pid) AS blockers
                            FROM pg_stat_activity WHERE pid=%s""", (state["pid"],)).fetchone()
                        if blocked and owner_pid in blocked["blockers"]:
                            break
                        c.execute("SELECT pg_stat_clear_snapshot()")
                        threading.Event().wait(0.01)
                    mark("existing-authority-fence-blocks-competing-legacy-claim",
                         blocked and owner_pid in blocked["blockers"])
                    key = c.execute("SELECT hashtextextended(%s,0) AS n",
                                    ("native-authority:" + str(actor["authority"]),)).fetchone()["n"]
                    locks = c.execute("""SELECT classid::bigint AS hi,objid::bigint AS lo,objsubid
                        FROM pg_locks WHERE pid=%s AND locktype='advisory' AND NOT granted""",
                                      (state["pid"],)).fetchall()
                    expected = {"hi": (key >> 32) & 0xffffffff, "lo": key & 0xffffffff, "objsubid": 1}
                    mark("observed-block-is-authority-fence-not-agent-lock", expected in locks)
                    report["competingClaim"] = {"blockedOnExistingAuthorityFence": True,
                                               "observedWaitingLock": expected,
                                               "meaning": "Existing fence, not a newly introduced race fix."}
                # Enrollment is committed before the blocked Store.claim resumes.
                result = future.result(timeout=10)
                mark("legacy-claim-observes-layout-after-existing-fence",
                     result.get("conflict") == "Purpose-context agent uses its native context runtime")
                report["competingClaim"]["result"] = result
        finally:
            claimant.pool = original_pool
            claimant.close()
        with store.pool.connection() as c:
            mark("copy-enrollment-same-agent-three-contexts",
                 c.execute("SELECT count(*) AS n FROM hb_purpose_context WHERE agent=%s",
                           (args.agent_id,)).fetchone()["n"] == 3)
            mark("legacy-history-unchanged-after-copy-commit",
                 c.execute("SELECT * FROM hb_agent_context WHERE principal=%s",
                           (args.agent_id,)).fetchone() == legacy)
        report.update(passed=True, disposableDatabase=database, committedMetadataOnly=True,
                      agentId=args.agent_id, liveChanges=False, at=time.time())
    except BaseException as error:
        report.update(passed=False, errorType=type(error).__name__, error=str(error), at=time.time())
        raise
    finally:
        store.close()
        args.output.write_text(json.dumps(report, indent=2, default=str) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-config", required=True, type=Path)
    parser.add_argument("--disposable-database", required=True,
                        help="Exact private copy name; metadata enrollment is committed here")
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--output", required=True, type=Path)
    main(parser.parse_args())
