"""Fresh RAM PostgreSQL + HTTP fences/races. All execution receipts are synthetic.

This is deliberately NOT the later real-native same-context acceptance proof.
Never point RECOVERY_DATABASE_CONFIG at production or a retained fixture.
"""
import asyncio
import hashlib
import json
import os
import secrets
from pathlib import Path
from uuid import uuid4

from aiohttp import ClientSession, web
from psycopg.types.json import Jsonb

from hollywood_backbone.server import application
from hollywood_backbone.store import Store, digest, json_value
from hollywood_backbone.native_effects import HEADER


def new_id():
    return str(uuid4())


async def main():
    import hollywood_backbone
    source_root = Path(__file__).resolve().parents[1]
    if not Path(hollywood_backbone.__file__).resolve().is_relative_to(source_root):
        raise RuntimeError("Qualification must import the exact owned staged source")
    root = Path(os.environ["RECOVERY_EVIDENCE"]).resolve()
    config = json.loads(Path(os.environ["RECOVERY_DATABASE_CONFIG"]).read_text())
    if (not str(root).startswith("/dev/shm/") or not config["host"].startswith("/dev/shm/")
            or config.get("dbname") != "report_recovery_fixture"):
        raise RuntimeError("New isolated RAM PostgreSQL fixture required")
    root.mkdir(mode=0o700)  # Fresh namespace only; never replay an earlier fixture.
    ids = {k: new_id() for k in ("authority", "room", "host", "controller")}
    credentials = {k: {"principalId": new_id(), "token": secrets.token_urlsafe(40)}
                   for k in ("Owner", "Global")}
    owner = credentials["Owner"]["principalId"]
    glob = credentials["Global"]["principalId"]
    store = Store(config)
    store.migrate()
    store.bootstrap(ids["authority"], "SYNTHETIC completed-report HTTP qualification",
                    {"globalRoomId": ids["room"], "credentials": credentials})
    store.principal(owner, ids["controller"], "Synthetic controller", "service",
                    secrets.token_urlsafe(40))
    checks, trace = [], []
    runner = web.AppRunner(application(store))
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    base = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/hollywood/v3/"

    def sql(statement, values=(), all_rows=False):
        with store.pool.connection() as c:
            result = c.execute(statement, values)
            if result.description:
                return result.fetchall() if all_rows else result.fetchone()

    async with ClientSession() as client:
        async def call(token, path, payload=None, source=None, status=200):
            headers = {"Authorization": "Bearer " + token}
            if source:
                headers[HEADER] = json.dumps(source)
            async with client.request("POST" if payload is not None else "GET",
                                      base + path, json=payload, headers=headers) as response:
                text = await response.text()
                try:
                    value = json.loads(text)
                except ValueError:
                    value = {"nonJsonResponse": text[:4000]}
                trace.append({"path": path, "status": response.status, "result": value})
                with (root / "http-trace.jsonl").open("a") as output:
                    output.write(json.dumps(trace[-1]) + "\n")
                assert response.status == status, (path, response.status, value, status)
                return value

        def fixture():
            f = {k: new_id() for k in ("agent", "room", "goal", "work", "workspace")}
            f["token"] = secrets.token_urlsafe(40)
            store.principal(owner, f["agent"], "Synthetic worker " + f["agent"], "agent", f["token"])
            store.create_room(owner, f["room"], "Fixture " + f["room"], "SYNTHETIC HTTP", f["agent"])
            store.placement(owner, ids["host"], "Synthetic host", "fixture", {}, f["agent"])
            if not sql("SELECT 1 FROM hb_host_admission WHERE host=%s", (ids["host"],)):
                sql("""INSERT INTO hb_host_admission(host,authority,controller,machine_identity,
                    max_concurrent,reserved_interactive,memory_per_turn,memory_reserve,
                    sample_max_age,connector_max_age) VALUES(%s,%s,%s,'fixture',16,1,1,0,300,300)""",
                    (ids["host"], ids["authority"], ids["controller"]))
            request = store.post(owner, ids["room"], new_id(), {
                "kind": "request", "body": "SYNTHETIC HTTP goal source; not a real human order",
                "audience": "room", "recipientId": glob})
            g = store.workflow(owner, "goal.create", ids["room"], new_id(), {
                "id": f["goal"], "sourceEventId": request["event"]["id"], "title": "Fixture",
                "objective": "Focused synthetic fences", "acceptance": "No native proof", "reviewBudget": 0})
            store.workflow(owner, "goal.work", f["goal"], new_id(), {
                "id": f["work"], "revision": g["goalRevision"], "roomId": f["room"],
                "ownerId": f["agent"], "specification": "Retain synthetic original",
                "acceptance": "Evidence and independent review", "dependencies": []})
            assigned = store.workflow(owner, "work.assign", f["work"], new_id(), {"revision": 1})
            f["prior"] = assigned["deliveryId"]
            f["priorRequest"] = assigned["requestEventId"]
            sql("UPDATE hb_work_attempt SET briefing_sequence=0 WHERE delivery=%s", (f["prior"],))
            current = store.post(owner, f["room"], new_id(), {"kind": "request", "body": "SYNTHETIC later normal participation",
                "audience": "room", "recipientId": f["agent"]})
            f["current"] = current["deliveries"][0]["id"]
            f["currentRequest"] = current["event"]["id"]
            context = hashlib.sha256(f["agent"].encode()).hexdigest()
            thread = "fixture-thread-" + f["agent"]
            role = store.role_read(owner, f["agent"])["revision"]
            sql("INSERT INTO hb_agent_context(principal,authority,context_id,native_thread,host,workspace_id) VALUES(%s,%s,%s,%s,%s,%s)",
                (f["agent"], ids["authority"], context, thread, ids["host"], f["workspace"]))
            with store.pool.connection() as c:
                actor = store.actor(c, f["agent"])
                result = store.event(c, actor, f["room"], "result", "SYNTHETIC completion", source=f["priorRequest"],
                    data={"type": "executionState", "state": "completed", "deliveryId": f["prior"], "generation": 1})
                for delivery, state, result_id in [(f["prior"], "completed", result), (f["current"], "running", None)]:
                    claim, turn = "claim-" + delivery, "turn-" + delivery
                    c.execute("UPDATE hb_delivery SET state=%s,host=%s,generation=1,claim_id=%s,native_thread=%s,native_turn=%s,result_event=%s WHERE id=%s",
                        (state, ids["host"], claim, thread, turn, result_id, delivery))
                    c.execute("INSERT INTO hb_agent_execution VALUES(%s,1,%s,%s,%s,%s,%s,%s)",
                        (delivery, f["agent"], context, thread, f["workspace"], role, digest(delivery)))
                    c.execute("""INSERT INTO hb_executor(id,authority,delivery,agent,host,generation,claim_id,
                        descriptor,descriptor_digest,state,controller,evidence,observed_at)
                        VALUES(%s,%s,%s,%s,%s,1,%s,'{}',%s,%s,%s,%s,clock_timestamp())""",
                        (new_id(), ids["authority"], delivery, f["agent"], ids["host"], claim, digest(delivery),
                         "stopped" if state == "completed" else "live", ids["controller"], Jsonb({"syntheticFixture": True})))
            f["source"] = {"schema": "hollywood-native-source/v2", "authorityId": ids["authority"],
                "agentId": f["agent"], "hostId": ids["host"], "deliveryId": f["current"],
                "claimId": "claim-" + f["current"], "generation": 1, "requestId": f["currentRequest"],
                "nativeThread": thread, "nativeTurn": "turn-" + f["current"], "callId": new_id(),
                "tool": "hollywood_work_report_reconcile", "argumentsDigest": "a" * 64,
                "roleRevision": role, "contextId": context, "nativeGoalId": None}
            f["payload"] = {"operationId": new_id(), "revision": 2, "priorDeliveryId": f["prior"],
                "state": "submitted", "effectDisposition": "reviewed", "note": "Synthetic effect ledger inspected; no original replay.",
                "evidence": [{"hostId": ids["host"], "workspaceId": f["workspace"], "relativePath": "fixture.json", "sha256": "b" * 64, "size": 12}]}
            return f

        async def submit(f, payload=None, source=None, status=200):
            return await call(f["token"], f"work/{f['work']}/report-reconcile",
                              payload or f["payload"], source or f["source"], status)

        try:
            f = fixture()
            sql("""INSERT INTO hb_native_effect(authority,principal,delivery,generation,fingerprint,
                source,method,path,wire_digest,operation) VALUES(%s,%s,%s,1,%s,%s,'POST',
                'synthetic-original-effect',%s,%s)""",
                (ids["authority"], f["agent"], f["prior"], digest(new_id()),
                 Jsonb({"syntheticFixtureOnly": True}), "d" * 64, new_id()))
            original_effects = sql("SELECT * FROM hb_native_effect WHERE delivery=%s ORDER BY sequence",
                                   (f["prior"],), all_rows=True)
            immutable = {"delivery": sql("SELECT * FROM hb_delivery WHERE id=%s", (f["prior"],)),
                         "context": sql("SELECT * FROM hb_agent_context WHERE principal=%s", (f["agent"],)),
                         "binding": sql("SELECT * FROM hb_agent_execution WHERE delivery=%s", (f["prior"],))}
            pre_report_head = sql("SELECT next_seq FROM hb_authority WHERE id=%s", (ids["authority"],))["next_seq"]
            r = await submit(f)
            assert r["state"] == "submitted" and r["deliveryId"] == f["prior"]
            assert (await submit(f)) == r
            await submit(f, {**f["payload"], "note": "Changed payload"}, status=409)
            assert sql("SELECT * FROM hb_delivery WHERE id=%s", (f["prior"],)) == immutable["delivery"]
            assert sql("SELECT * FROM hb_agent_context WHERE principal=%s", (f["agent"],)) == immutable["context"]
            assert sql("SELECT * FROM hb_agent_execution WHERE delivery=%s", (f["prior"],)) == immutable["binding"]
            assert sql("SELECT * FROM hb_native_effect WHERE delivery=%s ORDER BY sequence",
                       (f["prior"],), all_rows=True) == original_effects
            assert sql("SELECT count(*) AS n FROM hb_work_attempt WHERE work=%s", (f["work"],))["n"] == 1
            assert sql("SELECT count(*) AS n FROM hb_delivery WHERE agent=%s", (f["agent"],))["n"] == 2
            history = await call(credentials["Owner"]["token"], f"work/{f['work']}/attempts")
            assert history["attempts"][0]["report_reconciliation_event"] == r["eventId"]
            assert history["attempts"][0]["briefing_sequence"] == 0
            assert history["attempts"][0]["report_reconciliation"]["originalBriefingSequence"] == 0
            from hollywood_backbone.portfolio import material
            with store.pool.connection() as c:
                c.execute("""INSERT INTO hb_portfolio_policy
                    (principal,authority,room,enabled,activation_seq,scanned_seq,reviews_per_window,window_seconds)
                    VALUES(%s,%s,%s,false,%s,%s,1,60)""",
                    (glob, ids["authority"], ids["room"], pre_report_head, pre_report_head))
                material(c, store.actor(c, glob), {"scanned_seq": pre_report_head,
                                                   "activation_seq": pre_report_head})
            assert str(sql("SELECT source_event FROM hb_portfolio_item WHERE principal=%s AND reference=%s",
                           (glob, f["goal"]))["source_event"]) == r["eventId"]
            await call(f["token"], f"work/{f['work']}/review", {"operationId": new_id(), "revision": 3, "decision": "accepted", "note": "Cannot self-accept"},
                       {**f["source"], "tool": "hollywood_work_review"}, 403)
            await call(credentials["Owner"]["token"], f"work/{f['work']}/review", {"operationId": new_id(), "revision": 3, "decision": "accepted", "note": "Distinct authorized synthetic reviewer inspected evidence"})
            checks.append("Original delivery/context/binding/attempt count unchanged; explicit submitted evidence; stable replay/conflict; distinct review only")

            f = fixture()
            ordinary = {k: v for k, v in f["payload"].items() if k not in ("priorDeliveryId", "effectDisposition")}
            ordinary.update(claimId="claim-" + f["prior"], generation=1)
            await call(f["token"], f"work/{f['work']}/report", ordinary, {**f["source"], "tool": "hollywood_work_report"}, 409)
            checks.append("Ordinary reporting remains strict for completed originals")

            f = fixture()
            sql("UPDATE hb_delivery SET state='running' WHERE id=%s", (f["prior"],))
            sql("UPDATE hb_executor SET state='live' WHERE delivery=%s", (f["prior"],))
            original_source = {**f["source"], "deliveryId": f["prior"],
                "requestId": f["priorRequest"], "claimId": "claim-" + f["prior"],
                "nativeTurn": "turn-" + f["prior"], "tool": "hollywood_work_report"}
            ordinary = {k: v for k, v in f["payload"].items() if k not in ("priorDeliveryId", "effectDisposition")}
            ordinary.update(claimId="claim-" + f["prior"], generation=1, briefingSequence=0)
            await call(f["token"], f"work/{f['work']}/report", ordinary, original_source)
            review = {"operationId": new_id(), "revision": 3, "decision": "accepted", "note": "Synthetic independent ordinary review"}
            await call(credentials["Owner"]["token"], f"work/{f['work']}/review", review, status=409)
            sql("UPDATE hb_delivery SET state='completed' WHERE id=%s", (f["prior"],))
            await call(credentials["Owner"]["token"], f"work/{f['work']}/review", review)
            checks.append("Ordinary live-original report unchanged; independent review waits for original completion and uses ordinary briefing history")

            # A later decision read is additive provenance, never original history.
            f = fixture()
            store.decision_write(owner, f["room"], new_id(), {
                "id": new_id(), "key": "later-acceptance", "revision": 0, "goalId": None,
                "state": "active", "body": "Synthetic later evidence requirement",
                "sourceEventId": f["priorRequest"]})
            await submit(f, {**f["payload"], "briefingSequence": 0}, status=409)
            head = sql("SELECT next_seq FROM hb_authority WHERE id=%s", (ids["authority"],))["next_seq"]
            reconciled = await submit(f, {**f["payload"], "briefingSequence": head})
            original_attempt = sql("SELECT * FROM hb_work_attempt WHERE delivery=%s", (f["prior"],))
            assert original_attempt["briefing_sequence"] == 0
            assert reconciled["reconciliation"]["originalBriefingSequence"] == 0
            assert reconciled["reconciliation"]["briefingSequence"] == head and head > 0
            await call(credentials["Owner"]["token"], f"work/{f['work']}/review", {
                "operationId": new_id(), "revision": 3, "decision": "accepted",
                "note": "Distinct synthetic reviewer uses later audit, preserving historical briefing"})
            checks.append("HISTORY-1: original briefing0 preserved; later decision sequence in additive audit enables independent current review")

            f = fixture()
            decision = {"id": new_id(), "key": "audit-read", "revision": 0, "goalId": None,
                        "state": "active", "body": "Synthetic current requirement", "sourceEventId": f["priorRequest"]}
            store.decision_write(owner, f["room"], new_id(), decision)
            head = sql("SELECT next_seq FROM hb_authority WHERE id=%s", (ids["authority"],))["next_seq"]
            await submit(f, {**f["payload"], "briefingSequence": head})
            store.decision_write(owner, f["room"], new_id(), {**decision, "revision": 1, "body": "Synthetic newer requirement"})
            await call(credentials["Owner"]["token"], f"work/{f['work']}/review", {
                "operationId": new_id(), "revision": 3, "decision": "accepted", "note": "Stale later audit must not accept"}, status=409)
            assert sql("SELECT briefing_sequence FROM hb_work_attempt WHERE delivery=%s", (f["prior"],))["briefing_sequence"] == 0
            checks.append("New decisions after reconciled submission fence acceptance without rewriting original history")

            for change in ({"claimId": "stale"}, {"generation": 2}, {"agentId": glob},
                           {"hostId": new_id()}, {"contextId": "f" * 64}, {"roleRevision": 999},
                           {"tool": "hollywood_work_report"}):
                f = fixture()
                await submit(f, source={**f["source"], **change}, status=403 if "agentId" in change or "tool" in change else 409)
            for state in ("failed", "unknown", "running", "cancelled"):
                f = fixture()
                sql("UPDATE hb_delivery SET state=%s WHERE id=%s", (state, f["prior"]))
                await submit(f, status=409)
            checks.append("Stale current claim/generation/actor/host/context/role/tool and non-completed originals denied")

            for kind in ("revision", "prior", "host", "workspace", "evidence", "effects", "owner", "cancel", "paused", "decisions", "rights", "resource", "context", "reported", "token"):
                f = fixture(); payload = json.loads(json.dumps(f["payload"])); expected = 409
                if kind == "revision": payload["revision"] = 1
                elif kind == "prior": payload["priorDeliveryId"] = new_id()
                elif kind in ("host", "workspace"):
                    payload["evidence"][0]["hostId" if kind == "host" else "workspaceId"] = new_id(); expected = 403
                elif kind == "evidence": payload["evidence"] = []
                elif kind == "effects": payload["effectDisposition"] = "unknown"
                elif kind == "owner": sql("UPDATE hb_work SET owner=%s WHERE id=%s", (glob, f["work"])); expected = 403
                elif kind == "cancel": sql("UPDATE hb_goal SET state='cancelled' WHERE id=%s", (f["goal"],))
                elif kind == "paused":
                    sql("UPDATE hb_goal SET state='paused' WHERE id=%s", (f["goal"],)); expected = 200
                elif kind == "decisions": payload["briefingSequence"] = 9007199254740991
                elif kind == "rights": sql("UPDATE hb_membership SET can_post=false WHERE principal=%s AND room=%s", (f["agent"], f["room"])); expected = 403
                elif kind == "resource": sql("UPDATE hb_executor SET state='unknown' WHERE delivery=%s", (f["prior"],))
                elif kind == "context": sql("UPDATE hb_agent_execution SET workspace_id=%s WHERE delivery=%s", (new_id(), f["prior"]))
                elif kind == "reported": sql("UPDATE hb_work SET state='submitted' WHERE id=%s", (f["work"],))
                elif kind == "token": sql("UPDATE hb_token SET revoked=true WHERE principal=%s", (f["agent"],)); expected = 403
                await submit(f, payload, status=expected)
            checks.append("CAS/supersession/evidence/effect attestation/ownership/cancellation/revocation/binding/resource fences; paused evidence allowed")

            f = fixture()
            store.decision_write(owner, f["room"], new_id(), {
                "id": new_id(), "key": "current-evidence", "revision": 0,
                "goalId": None, "state": "conflicted", "body": "Synthetic conflicting acceptance",
                "sourceEventId": f["priorRequest"]})
            await submit(f, status=409)  # Stale or absent decision briefing.
            head = sql("SELECT next_seq FROM hb_authority WHERE id=%s", (ids["authority"],))["next_seq"]
            await submit(f, {**f["payload"], "briefingSequence": head}, status=409)
            blocked = await submit(f, {**f["payload"], "state": "blocked", "effectDisposition": "unknown", "briefingSequence": head})
            assert blocked["reconciliation"]["unresolvedEffects"]["decisions"]
            await call(credentials["Owner"]["token"], f"work/{f['work']}/review", {"operationId": new_id(), "revision": 3, "decision": "accepted", "note": "No acceptance of uncertain blocked work"}, status=409)
            checks.append("Current decision conflicts remain explicit blocked evidence and cannot be accepted")

            f = fixture()
            sql("""INSERT INTO hb_native_control(id,authority,delivery,agent,human,room,event,generation,
                claim_id,native_thread,native_turn,operation,body,digest,captured_scope,state)
                VALUES(%s,%s,%s,%s,%s,%s,%s,1,%s,%s,%s,%s,'Synthetic unknown effect',%s,'{}','unknown')""",
                (new_id(), ids["authority"], f["prior"], f["agent"], owner, f["room"], f["priorRequest"],
                 "claim-" + f["prior"], f["source"]["nativeThread"], "turn-" + f["prior"], new_id(), "c" * 64))
            await submit(f, status=409)
            blocked = await submit(f, {**f["payload"], "state": "blocked", "effectDisposition": "unknown"})
            assert blocked["reconciliation"]["unresolvedEffects"]["controls"]
            assert sql("SELECT state FROM hb_native_control WHERE delivery=%s", (f["prior"],))["state"] == "unknown"
            checks.append("Unknown original controls preserved; blocked record only, no submitted success or effect rewrite")

            f = fixture()
            replies = await asyncio.gather(*(submit(f) for _ in range(6)))
            assert all(r == replies[0] for r in replies)
            assert sql("SELECT count(*) AS n FROM hb_work_report_reconciliation WHERE work=%s", (f["work"],))["n"] == 1
            checks.append("Concurrent same-operation HTTP retries produce exactly one report receipt")

            f = fixture()
            # Force cancellation to commit first, then exercise stale submission.
            await call(credentials["Owner"]["token"], f"work/{f['work']}/cancel", {"operationId": new_id(), "revision": 2, "note": "Synthetic stop-before-report"})
            await submit(f, status=409)
            f = fixture()
            async def racing_report():
                async with client.post(base + f"work/{f['work']}/report-reconcile", json=f["payload"], headers={"Authorization": "Bearer " + f["token"], HEADER: json.dumps(f["source"])}) as response:
                    return response.status
            async def racing_cancel():
                async with client.post(base + f"work/{f['work']}/cancel", json={"operationId": new_id(), "revision": 2, "note": "Synthetic race stop"}, headers={"Authorization": "Bearer " + credentials["Owner"]["token"]}) as response:
                    return response.status
            statuses = await asyncio.gather(racing_report(), racing_cancel())
            assert sorted(statuses) == [200, 409], statuses
            assert sql("SELECT count(*) AS n FROM hb_work_attempt WHERE work=%s", (f["work"],))["n"] == 1
            race = json_value({"workId": f["work"], "originalDeliveryId": f["prior"],
                "currentDeliveryId": f["current"], "reportStatus": statuses[0],
                "cancelStatus": statuses[1],
                "finalWork": sql("SELECT * FROM hb_work WHERE id=%s", (f["work"],)),
                "reconciliation": sql("SELECT * FROM hb_work_report_reconciliation WHERE work=%s", (f["work"],)),
                "attemptCount": sql("SELECT count(*) AS n FROM hb_work_attempt WHERE work=%s", (f["work"],))["n"],
                "executionReceipts": "SYNTHETIC FIXTURE ONLY"})
            (root / "race-result.json").write_text(json.dumps(race, indent=2))
            checks.append("Report-vs-cancel serialized by authority/goal/CAS; cancellation-first fences stale submission; no second attempt/admission")
            result = {"state": "passed", "actualPostgreSQL": True, "authenticatedHTTP": True,
                      "nativeReceipts": "SYNTHETIC FIXTURE ONLY", "modelsStarted": 0,
                      "authorityId": ids["authority"], "checks": checks,
                      "liveRuntimeChanges": False, "nativeAcceptanceStillRequired": True}
            (root / "result.json").write_text(json.dumps(result, indent=2))
            (root / "http-trace.json").write_text(json.dumps(trace, indent=2))
            print(json.dumps(result))
        finally:
            await runner.cleanup()
            store.close()


if __name__ == "__main__":
    asyncio.run(main())
