"""Real PostgreSQL native claim integration; temporary inputs all roll back.

Synthetic capacity/turn facts qualify substrate only. This starts no model,
native process, installed service, or business task.
"""
import argparse
import contextlib
import datetime
import json
from pathlib import Path
from uuid import uuid4

from psycopg.types.json import Jsonb
from hollywood_backbone import admission
from hollywood_backbone.claims import claim
from hollywood_backbone.context_admission import admit, ready
from hollywood_backbone.context_system import SCHEMA, perform
from fixture_context_capacity import lifecycle
from hollywood_backbone.store import Store, Conflict, Denied


def main(args):
    store = Store(json.loads(args.database_config.read_text()))
    proof = {"schema": "lax-purpose-native-admission-pg/v1", "modelsStarted": 0,
             "mode": "Real PostgreSQL, rollback-only synthetic capacity and native turn facts"}
    try:
        with store.pool.connection() as c:
            c.execute(SCHEMA)
            actor = store.actor(c, args.agent_id)
            before = counts(c, actor)
            system = c.execute("SELECT * FROM hb_context_system WHERE agent=%s",
                               (actor["id"],)).fetchone()
            host = str(system["host"])
            role = c.execute("SELECT * FROM hb_agent_role WHERE principal=%s",
                             (actor["id"],)).fetchone()
            owner = c.execute("""SELECT * FROM hb_principal WHERE authority=%s
                AND kind='human' AND administrator AND active LIMIT 1""",
                (actor["authority"],)).fetchone()
            reader = binding(c, actor, "reader")
            writer = binding(c, actor, "writer")
            assert reader["active_turn"] is None and writer["active_turn"] is None
            caps = sorted(admission.CAPABILITIES)

            def request(label):
                return store.post_in_transaction(c, owner, str(role["home_room"]),
                    "rollback-native-admission:" + str(uuid4()), {
                        "kind": "request", "audience": "room",
                        "recipientId": str(actor["id"]), "body": label})

            status = request("Disposable status input; do not execute")
            source = request("Disposable accepted writer work; do not execute")
            source_id = source["event"]["id"]
            delivery_id = source["deliveries"][0]["id"]
            observation = admission.diagnose(store, c, actor, host, caps)
            assert observation["pending"] is None, observation

            # This is model output frozen at the protocol boundary, not proof
            # that a real reader would choose these actions.
            reader_turn = "rollback-reader:" + str(uuid4())
            p = params(reader, reader_turn, "begin", {"materialIds": [source_id]})
            lifecycle(c, store, actor, "reader", reader, p)
            accepted = perform(store, c, actor, "reader", binding(c, actor, "reader"),
                params(reader, reader_turn, "accept", {"inputId": source_id,
                    "objective": "Disposable admission contract", "acceptance": "No execution"}))
            work_id = accepted["workId"]
            d = store.claim_receipt(c, delivery_id)["delivery"]
            assert (d["state"], d["generation"], d["claim_id"], d["host"]) == ("pending", 0, None, None)
            assert not c.execute("SELECT 1 FROM hb_claim WHERE delivery=%s", (delivery_id,)).fetchone()
            lifecycle(c, store, actor, "reader", binding(c, actor, "reader"),
                params(reader, reader_turn, "end", {"status": "completed"}))

            operation = "rollback-writer-admit:" + str(uuid4())
            p = params(writer, "pre-inference", "admit", {"workId": work_id, "capabilities": caps})
            p["operationId"] = operation
            # No capacity attestation can be fabricated by accepting input.
            c.execute("DELETE FROM hb_host_sample WHERE host=%s", (host,))
            denied = admit(store, c, actor, writer, p)
            assert denied["delivery"] is None, denied
            assert not c.execute("SELECT 1 FROM hb_claim WHERE delivery=%s", (delivery_id,)).fetchone()
            assert store.claim_receipt(c, delivery_id)["delivery"]["state"] == "pending"

            controller = str(uuid4())
            c.execute("""INSERT INTO hb_principal(id,authority,name,kind)
                VALUES(%s,%s,%s,'service')""",
                (controller, actor["authority"], "rollback-controller-" + controller))
            c.execute("""INSERT INTO hb_host_admission
                (host,authority,controller,machine_identity,max_concurrent,reserved_interactive,
                 memory_per_turn,memory_reserve,sample_max_age,connector_max_age)
                VALUES(%s,%s,%s,%s,4,0,1048576,0,300,300)
                ON CONFLICT(host) DO UPDATE SET controller=EXCLUDED.controller,
                  max_concurrent=4,reserved_interactive=0,memory_per_turn=1048576,
                  memory_reserve=0,sample_max_age=300,connector_max_age=300""",
                (host, actor["authority"], controller, "rollback-capacity-not-native-evidence"))
            c.execute("""INSERT INTO hb_host_sample(host,cpu_slots,available_memory)
                VALUES(%s,4,1073741824) ON CONFLICT(host) DO UPDATE SET cpu_slots=4,
                available_memory=1073741824,observed_at=clock_timestamp()""", (host,))
            native_claim = admit(store, c, actor, writer, p)
            d = native_claim["delivery"]
            assert (d["id"], d["state"], d["claim_id"], d["generation"]) == (
                delivery_id, "claimed", operation, 1), native_claim
            assert native_claim["admissionTrace"]["ticket"] is not None
            assert native_claim["admission"]["reason"] == "eligible"
            assert native_claim["admission"]["eligibleDeliveryId"] == delivery_id
            assert store.claim_receipt(c, status["deliveries"][0]["id"])["delivery"]["state"] == "pending"
            repeat = claim(store, c, actor, operation, host, caps, expected_delivery=delivery_id)
            assert repeat["delivery"] == native_claim["delivery"]
            assert c.execute("SELECT count(*) AS n FROM hb_claim WHERE delivery=%s",
                             (delivery_id,)).fetchone()["n"] == 1
            try:
                claim(store, c, actor, operation, host, caps, expected_delivery=status["deliveries"][0]["id"])
            except Conflict:
                pass
            else:
                raise AssertionError("Claim could be retargeted to another input")

            scopes = []
            for _ in range(2):
                turn = "rollback-writer:" + str(uuid4())
                current = binding(c, actor, "writer")
                scopes.append(lifecycle(c, store, actor, "writer", current,
                    params(current, turn, "begin", {"materialIds": [work_id]}))["executionScopes"][0])
                lifecycle(c, store, actor, "writer", binding(c, actor, "writer"),
                    params(current, turn, "end", {"status": "completed"}))
                continued = admit(store, c, actor, binding(c, actor, "writer"), p)
                assert continued["delivery"]["generation"] == 1
                assert continued["delivery"]["claim_id"] == operation
            assert scopes[0]["nativeTurn"] != scopes[1]["nativeTurn"]
            assert {s["deliveryId"] for s in scopes} == {delivery_id}
            assert {s["generation"] for s in scopes} == {1}
            # Run the public legacy Store.claim against this same real database
            # transaction, preserving rollback instead of committing QA inputs.
            original_pool = store.pool
            class RollbackPool:
                @contextlib.contextmanager
                def connection(self):
                    with c.transaction():
                        yield c
            store.pool = RollbackPool()
            try:
                try:
                    store.claim(str(actor["id"]), "rollback-wrong-launcher", host, caps)
                except Conflict:
                    pass
                else:
                    raise AssertionError("Legacy launcher claimed a purpose agent")
                legacy_id = str(uuid4())
                store._admin_principal(c, owner, legacy_id, "rollback-legacy-" + legacy_id,
                                       "agent", "rollback-only-credential-" + str(uuid4()))
                c.execute("""INSERT INTO hb_membership(authority,room,principal,can_read,can_post,can_request)
                    VALUES(%s,%s,%s,true,true,true)""", (actor["authority"], role["home_room"], legacy_id))
                c.execute("INSERT INTO hb_placement(authority,agent,host,enabled) VALUES(%s,%s,%s,true)",
                          (actor["authority"], legacy_id, host))
                legacy = store.actor(c, legacy_id)
                legacy_source = store.post_in_transaction(c, owner, str(role["home_room"]),
                    "rollback-legacy-input:" + str(uuid4()), {
                        "kind": "request", "audience": "room",
                        "recipientId": legacy_id, "body": "Rollback-only original legacy claim"})
                legacy_claim = store.claim(legacy_id, "rollback-legacy-claim", host, caps)
                assert legacy_claim["delivery"]["id"] == legacy_source["deliveries"][0]["id"]
                assert legacy_claim["delivery"]["state"] == "claimed"
                assert legacy_claim["admission"]["reason"] == "eligible"
            finally:
                store.pool = original_pool
            c.execute("UPDATE hb_delivery SET cancellation_requested=true WHERE id=%s", (delivery_id,))
            try:
                ready(store, c, actor, binding(c, actor, "writer"),
                      {**p, "value": {"workId": work_id}})
            except Conflict:
                pass
            else:
                raise AssertionError("Cancelled original admitted")
            try:
                perform(store, c, actor, "reader", binding(c, actor, "reader"), p)
            except Denied:
                pass
            else:
                raise AssertionError("Reader admitted writer work")
            proof.update({
                "interpretationReservedCapacity": False,
                "uninterpretedEarlierChatBlockedWriter": False,
                "unavailableCapacityLeftWorkPending": True,
                "normalNativeClaimAndFairQueueReceipt": True,
                "duplicateClaimReceiptStable": True,
                "retargetedClaimDenied": True,
                "successiveTurnsRetainedOriginalClaimAndGeneration": True,
                "cancellationPreventedAdmission": True,
                "currentAuthorizationCheckedBeyondCachedClaim": True,
                "readerAdmissionDenied": True,
                "legacyStoreClaimUsesSharedTransaction": True,
                "legacyLauncherStillDeniedForPurposeAgent": True,
                "mutationAuthorityGranted": False,
                "installedExecutorQualified": False,
            })
            c.rollback()
            assert counts(c, actor) == before
            proof["allSyntheticRecordsRolledBack"] = True
    finally:
        store.close()
    proof["at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    args.evidence_output.write_text(json.dumps(proof, indent=2) + "\n")
    print(json.dumps(proof))


def counts(c, actor):
    tables = ("hb_event", "hb_delivery", "hb_claim", "hb_context_material", "hb_principal")
    return {t: c.execute("SELECT count(*) AS n FROM " + t).fetchone()["n"] for t in tables}


def binding(c, actor, purpose):
    return c.execute("SELECT * FROM hb_purpose_context WHERE agent=%s AND purpose=%s",
                     (actor["id"], purpose)).fetchone()


def params(b, turn, action, value):
    return {"operationId": "rollback:" + str(uuid4()), "action": action,
            "nativeThread": b["native_thread"], "nativeTurn": turn, "value": value}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-config", type=Path, required=True)
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--evidence-output", type=Path, required=True)
    main(parser.parse_args())
