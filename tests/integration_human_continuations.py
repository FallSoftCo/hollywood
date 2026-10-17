"""Real PostgreSQL + authenticated HTTP question continuation contract.

Native process, provider and capacity facts are explicit metadata fixtures.
This does not claim model behavior, phone delivery or a production rollout.
"""
import asyncio
import hashlib
import json
import os
import secrets
from pathlib import Path
from uuid import uuid4

from aiohttp import ClientSession, web
from hollywood_backbone.server import application
from hollywood_backbone.store import Store
from hollywood_backbone.admission import CAPABILITIES as ADMISSION_CAPABILITIES
from fixture_admission import prepare, CAPABILITIES

ROOT = Path(os.environ["QUESTION_EVIDENCE"])
DB = Path(os.environ["QUESTION_DATABASE_CONFIG"])


async def main():
    os.umask(0o077)
    ROOT.mkdir(mode=0o700)
    ids = {name: str(uuid4()) for name in ("authority", "room", "host", "workspace", "viewer")}
    credentials = {name: {"principalId": str(uuid4()), "token": secrets.token_urlsafe(40)}
                   for name in ("Owner", "Global")}
    tokens = {name: value["token"] for name, value in credentials.items()}
    tokens["Viewer"] = secrets.token_urlsafe(40)
    (ROOT / "intent.json").write_text(json.dumps(
        {"ids": ids, "credentials": credentials, "viewerToken": tokens["Viewer"],
         "scope": "new isolated metadata authority only; no models or Linux agents"}))
    store = Store(json.loads(DB.read_text()))
    # Reuse the existing owned QA schema. This test changes no schema or old authority.
    with store.pool.connection() as c:
        assert c.execute("SELECT max(version) AS version FROM hb_schema").fetchone()["version"] == 33
    store.bootstrap(ids["authority"], "Native human continuation HTTP contract",
                    {"globalRoomId": ids["room"], "credentials": credentials})
    runner = None
    trace, checks = [], []

    async def start():
        nonlocal runner
        runner = web.AppRunner(application(store))
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        return "http://127.0.0.1:" + str(site._server.sockets[0].getsockname()[1]) + "/hollywood/v3/"

    base = await start()
    try:
        async with ClientSession() as session:
            async def call(who, method, path, payload=None, status=200):
                async with session.request(
                    method, base + path, json=payload,
                    headers={"Authorization": "Bearer " + tokens[who]},
                ) as response:
                    value = await response.json()
                    trace.append({"who": who, "method": method, "path": path,
                                  "status": response.status, "response": value})
                    (ROOT / "http-trace.json").write_text(json.dumps(trace, default=str))
                    assert response.status == status, (path, response.status, value)
                    return value

            global_id = credentials["Global"]["principalId"]
            await call("Owner", "POST", "principals",
                       {"id": ids["viewer"], "name": "Other human fixture", "kind": "human",
                        "enrollmentCredential": tokens["Viewer"]})
            await call("Owner", "PUT", "rooms/" + ids["room"] + "/membership",
                       {"principalId": ids["viewer"], "can_read": True, "can_post": True,
                        "can_request": False, "revision": 0})
            await call("Owner", "POST", "placements",
                       {"hostId": ids["host"], "name": "Synthetic capacity only",
                        "identityFingerprint": hashlib.sha256(ids["host"].encode()).hexdigest(),
                        "capabilities": {"fixtureOnly": True}, "agentId": global_id})
            prepare(store, credentials["Owner"]["principalId"], ids["host"], ROOT)
            role = await call("Global", "GET", "agents/" + global_id + "/role")
            thread = "fixture-human-continuation-thread"
            context_id = hashlib.sha256(b"fixture-persistent-context").hexdigest()

            async def post(name):
                return await call("Owner", "POST", "rooms/" + ids["room"] + "/messages",
                                  {"operationId": name, "kind": "request", "audience": "room",
                                   "recipientId": global_id, "body": name})

            async def claim(name):
                value = await call("Global", "POST", "connector/claim",
                                   {"operationId": name, "hostId": ids["host"],
                                    "capabilities": sorted({*CAPABILITIES, *ADMISSION_CAPABILITIES, "persistent-agent-context/v1"})})
                d = value["delivery"]
                assert d and d["state"] == "claimed"
                await call("Global", "POST", "connector/deliveries/" + d["id"] + "/agent-binding",
                           {"claimId": d["claim_id"], "generation": d["generation"],
                            "nativeThread": thread, "contextId": context_id,
                            "workspaceId": ids["workspace"], "roleRevision": role["revision"]})
                return d

            async def transition(d, state, name):
                return await call("Global", "POST", "deliveries/" + d["id"] + "/transition",
                                  {"operationId": name, "claimId": d["claim_id"],
                                   "generation": d["generation"], "state": state,
                                   "nativeThread": thread, "nativeTurn": "fixture-" + d["id"],
                                   **({"body": "Metadata input handling " + state}
                                      if state in ("completed", "failed", "cancelled") else {})})

            async def question(d, blocking):
                return await call("Global", "POST", "attention",
                                  {"id": str(uuid4()), "deliveryId": d["id"],
                                   "claimId": d["claim_id"], "generation": d["generation"],
                                   "nativeThread": thread, "nativeTurn": "fixture-" + d["id"],
                                   "nativeSlot": "audience", "method": "hollywood/question",
                                   "summary": "Choose briefing audience",
                                   "request": {"isBlocking": blocking, "questions": [
                                       {"id": "audience", "header": "Audience",
                                        "question": "Technical operator or executive?",
                                        "options": [{"label": "Technical", "description": "Commands"},
                                                    {"label": "Executive", "description": "Outcomes"}]}]}})

            async def read_question(value):
                return await call("Owner", "GET", "attention/" + value["attention"]["id"])

            await post("original-question-task")
            d = await claim("original-question-claim")
            await transition(d, "running", "original-running")
            q = await read_question(await question(d, False))
            assert q["responseMode"] == "same-agent-context-input" and q["canRespond"]
            initial_hint = await call("Owner", "GET", "inbox/events/" + q["attention"]["event"])
            await transition(d, "completed", "original-input-completed")
            q = await read_question(q)
            assert q["attention"]["state"] == "pending" and q["canRespond"] and q["deliveryState"] == "completed"
            checks.append("Question remains durable and actionable after input handling completes")

            await post("independent-obligation")
            independent = await claim("independent-claim")
            await transition(independent, "running", "independent-running")
            await transition(independent, "completed", "independent-completed")
            assert (await read_question(q))["attention"]["state"] == "pending"
            rooms = await call("Owner", "GET", "rooms")
            room = next(r for r in rooms["rooms"] if r["id"] == ids["room"])
            assert room["activity"]["waitingForHuman"] == 1 and room["activity"]["running"] == 0
            hint = await call("Owner", "GET", "inbox/events/" + q["attention"]["event"])
            assert hint["hint"]["notify"] and hint["hint"]["reason"] == "question"
            assert hint["hint"]["revision"] == initial_hint["hint"]["revision"]
            checks.append("Independent same-context input completes; catalog and inbox retain pending human question")

            await runner.cleanup()
            store.close()
            store = Store(json.loads(DB.read_text()))
            base = await start()
            q = await read_question(q)
            assert q["canRespond"]
            response = {"operationId": "answer-original-question", "scope": q["scope"],
                        "response": {"answers": {"audience": {"answers": ["Technical"]}}}}
            await call("Global", "POST", "attention/" + q["attention"]["id"] + "/response", response, 403)
            await call("Viewer", "GET", "attention/" + q["attention"]["id"], status=403)
            wrong = {**response, "scope": {**q["scope"], "generation": q["scope"]["generation"] + 1}}
            await call("Owner", "POST", "attention/" + q["attention"]["id"] + "/response", wrong, 409)
            accepted = await call("Owner", "POST", "attention/" + q["attention"]["id"] + "/response", response)
            assert accepted["continuation"]["state"] == "pending" and not accepted["canRespond"]
            original = await call("Owner", "GET", "attention/" + q["attention"]["id"] + "/commands/answer-original-question")
            assert original == accepted
            continuation = await call("Owner", "GET", "deliveries/" + accepted["continuation"]["id"])
            assert continuation["request"]["author"] == credentials["Owner"]["principalId"]
            assert continuation["request"]["data"]["authorKind"] == "human"
            assert continuation["request"]["audience"] == "direct"
            assert continuation["request"]["source"] == q["attention"]["event"]
            reply = await claim("claim-original-answer")
            assert reply["id"] == accepted["continuation"]["id"]
            await transition(reply, "running", "answer-running")
            assert (await read_question(q))["attention"]["state"] == "native_submitted"
            await transition(reply, "completed", "answer-completed")
            assert (await read_question(q))["attention"]["state"] == "closed"
            checks.append("Restart retains original question; authenticated scoped answer queues one human input and settles after admission")

            await post("blocking-callback-task")
            callback = await claim("blocking-callback-claim")
            await transition(callback, "running", "callback-running")
            old = await question(callback, True)
            assert old["responseMode"] == "native-callback"
            await transition(callback, "completed", "callback-completed")
            old = await read_question(old)
            assert old["attention"]["state"] == "expired" and not old["canRespond"]
            checks.append("Exact native callback still expires with its original execution")

            await post("queued-answer-stop-task")
            waiting = await claim("queued-answer-stop-claim")
            await transition(waiting, "running", "queued-stop-running")
            final = await question(waiting, False)
            await transition(waiting, "completed", "queued-stop-parent-completed")
            final = await read_question(final)
            stopped = await call("Owner", "POST", "attention/" + final["attention"]["id"] + "/response",
                                 {"operationId": "queued-answer-stop", "scope": final["scope"],
                                  "response": {"answers": {"audience": {"answers": ["Executive"]}}}})
            await call("Owner", "POST", "deliveries/" + stopped["continuation"]["id"] + "/cancel", {})
            final = await read_question(final)
            assert final["attention"]["state"] == "expired"
            assert final["continuation"]["state"] == "cancelled"
            checks.append("Cancelling a queued answer expires that original continuation without native execution")
            (ROOT / "result.json").write_text(json.dumps(
                {"passed": True, "checks": checks, "httpRequests": len(trace),
                 "authority": ids["authority"], "modelsStarted": 0,
                 "evidenceScope": "Actual PG/authenticated HTTP; native execution and capacity are synthetic",
                 "phoneOrModelBehaviorProved": False}, indent=2))
            print(json.dumps({"passed": True, "checks": checks, "httpRequests": len(trace), "modelsStarted": 0}))
    finally:
        if runner:
            await runner.cleanup()
        store.close()


if __name__ == "__main__":
    asyncio.run(main())
