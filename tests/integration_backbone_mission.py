"""Installed HTTP/PostgreSQL mission contracts. Native transitions are explicit fixtures."""

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
from fixture_admission import prepare as capacity_fixture, CAPABILITIES

CAPS = CAPABILITIES


async def main():
    os.umask(0o077)
    root = Path(os.environ["MISSION_EVIDENCE"])
    root.mkdir(mode=0o700)
    ids = {
        k: str(uuid4())
        for k in [
            "authority",
            "home",
            "room",
            "host",
            "workspace",
            "goal",
            "work",
            "unknownWork",
            "decision",
            "privateDecision",
        ]
    }
    people = {
        k: {"principalId": str(uuid4()), "token": secrets.token_urlsafe(40)}
        for k in ["Owner", "Global", "OtherHuman", "Worker", "Reviewer", "Observer"]
    }
    intent = {"ids": ids, "credentials": people, "globalRoomId": ids["home"]}
    (root / "intent.json").write_text(json.dumps(intent))
    store = Store(json.loads(Path(os.environ["MISSION_DATABASE_CONFIG"]).read_text()))
    store.migrate()
    store.bootstrap(
        ids["authority"],
        "Durable mission qualification",
        {**intent, "credentials": {k: people[k] for k in ["Owner", "Global"]}},
    )
    runner, base = None, ""
    checks, trace = [], []

    async def start():
        nonlocal runner, base
        runner = web.AppRunner(application(store))
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", 18774).start()
        base = "http://127.0.0.1:18774/hollywood/v3/"

    await start()
    async with ClientSession() as session:

        async def call(who, method, path, payload=None, expected=200):
            if payload is not None:
                (root / "last-intent.json").write_text(
                    json.dumps({"who": who, "method": method, "path": path, "payload": payload})
                )
            async with session.request(
                method,
                base + path,
                json=payload,
                headers={"Authorization": "Bearer " + people[who]["token"]},
            ) as r:
                value = await r.json()
                if method == "POST" and path == "placements" and r.status == 200:
                    capacity_fixture(store, people["Owner"]["principalId"], payload["hostId"], root)
                trace.append(
                    {"who": who, "method": method, "path": path, "status": r.status, "value": value}
                )
                (root / "trace.json").write_text(json.dumps(trace))
                assert r.status == expected, (who, path, r.status, value, expected)
                return value

        async def get(who, path, expected=200):
            return await call(who, "GET", path, expected=expected)

        async def post(who, path, payload, expected=200):
            return await call(who, "POST", path, payload, expected)

        async def work():
            return (await get("Owner", "work/" + ids["work"]))["work"]

        async def transition(claim, state):
            d = claim["delivery"]
            return await post(
                "Worker",
                "deliveries/" + d["id"] + "/transition",
                {
                    "operationId": str(uuid4()),
                    "generation": d["generation"],
                    "claimId": d["claim_id"],
                    "state": state,
                    "nativeThread": "explicit-http-fixture-" + d["id"],
                    "nativeTurn": "explicit-http-fixture-turn-" + d["id"],
                    "body": "Synthetic native receipt; this driver starts zero models",
                },
            )

        async def assign(identity):
            w = (await get("Owner", "work/" + identity))["work"]
            return await post(
                "Owner",
                "work/" + identity + "/assign",
                {"operationId": str(uuid4()), "revision": w["revision"]},
            )

        async def claim():
            return await post(
                "Worker",
                "connector/claim",
                {"operationId": str(uuid4()), "hostId": ids["host"], "capabilities": CAPS},
            )

        async def report(c, briefing, body):
            w = await work()
            return await post(
                "Worker",
                "work/" + ids["work"] + "/report",
                {
                    "operationId": str(uuid4()),
                    "revision": w["revision"],
                    "claimId": c["delivery"]["claim_id"],
                    "generation": c["delivery"]["generation"],
                    "briefingSequence": briefing["asOfSequence"],
                    "state": "submitted",
                    "note": "Explicit fixture artifact evidence",
                    "evidence": [
                        {
                            "hostId": ids["host"],
                            "workspaceId": ids["workspace"],
                            "relativePath": "fixture.txt",
                            "size": len(body),
                            "sha256": hashlib.sha256(body).hexdigest(),
                        }
                    ],
                },
            )

        async def review(decision, expected=200):
            w = await work()
            return await post(
                "Reviewer",
                "work/" + ids["work"] + "/review",
                {
                    "operationId": str(uuid4()),
                    "revision": w["revision"],
                    "decision": decision,
                    "note": "HTTP fixture review: " + decision,
                },
                expected,
            )

        try:
            for name in people:
                if name in ("Owner", "Global"):
                    continue
                await post(
                    "Owner",
                    "principals",
                    {
                        "id": people[name]["principalId"],
                        "name": name,
                        "kind": "human" if name == "OtherHuman" else "agent",
                        "enrollmentCredential": people[name]["token"],
                    },
                )
            await post(
                "Owner",
                "rooms",
                {
                    "id": ids["room"],
                    "name": "Mission Engineering",
                    "purpose": "Scoped mission, current decisions and explicit attempts",
                    "ownerId": people["Global"]["principalId"],
                },
            )
            for name in ["OtherHuman", "Worker", "Reviewer", "Observer"]:
                await call(
                    "Owner",
                    "PUT",
                    "rooms/" + ids["room"] + "/membership",
                    {
                        "principalId": people[name]["principalId"],
                        "can_read": True,
                        "can_post": True,
                        "can_request": name != "Observer",
                        "revision": 0,
                    },
                )
            await post(
                "Owner",
                "placements",
                {
                    "hostId": ids["host"],
                    "name": "mission-fixture-host",
                    "identityFingerprint": "fixture-host-no-native-executor",
                    "capabilities": {},
                    "agentId": people["Worker"]["principalId"],
                },
            )
            source = await post(
                "Owner",
                "rooms/" + ids["room"] + "/messages",
                {
                    "operationId": str(uuid4()),
                    "kind": "request",
                    "recipientId": people["Global"]["principalId"],
                    "body": "Deliver the scoped mission",
                },
            )
            source_id = source["event"]["id"]
            await post(
                "Owner",
                "rooms/" + ids["room"] + "/goals",
                {
                    "operationId": str(uuid4()),
                    "id": ids["goal"],
                    "sourceEventId": source_id,
                    "title": "Durable mission",
                    "objective": "Retain direction and original attempts",
                    "acceptance": "Current decision and separately accepted evidence",
                    "reviewBudget": 0,
                },
            )
            goal = (await get("Owner", "goals/" + ids["goal"]))["goal"]
            await post(
                "Owner",
                "goals/" + ids["goal"] + "/cooperation",
                {
                    "operationId": str(uuid4()),
                    "revision": goal["revision"],
                    "cooperationRevision": 0,
                    "policy": "leader_award",
                    "reviewerId": people["Reviewer"]["principalId"],
                    "executorIds": [people["Worker"]["principalId"]],
                },
            )
            decision = {
                "operationId": str(uuid4()),
                "id": ids["decision"],
                "key": "release.content",
                "revision": 0,
                "goalId": ids["goal"],
                "state": "active",
                "body": "Version one direction",
                "sourceEventId": source_id,
            }
            first = await post("Owner", "rooms/" + ids["room"] + "/decisions", decision)
            assert first == await post("Owner", "rooms/" + ids["room"] + "/decisions", decision)
            await post(
                "Owner",
                "rooms/" + ids["room"] + "/decisions",
                {**decision, "body": "Changed original operation"},
                409,
            )
            await post(
                "OtherHuman",
                "rooms/" + ids["room"] + "/decisions",
                {**decision, "operationId": str(uuid4()), "revision": 1},
                403,
            )
            private = await post(
                "OtherHuman",
                "rooms/" + ids["room"] + "/messages",
                {
                    "operationId": str(uuid4()),
                    "kind": "message",
                    "audience": "direct",
                    "recipientId": people["Worker"]["principalId"],
                    "body": "Private fixture evidence",
                },
            )
            await post(
                "Owner",
                "rooms/" + ids["room"] + "/decisions",
                {
                    **decision,
                    "id": ids["privateDecision"],
                    "key": "private.no-promotion",
                    "operationId": str(uuid4()),
                    "sourceEventId": private["event"]["id"],
                },
                403,
            )
            checks.append(
                "Decision CAS/original receipt, two-human issuer boundary and private-source promotion denied"
            )

            for key in ["work", "unknownWork"]:
                goal = (await get("Owner", "goals/" + ids["goal"]))["goal"]
                await post(
                    "Owner",
                    "goals/" + ids["goal"] + "/work",
                    {
                        "operationId": str(uuid4()),
                        "revision": goal["revision"],
                        "id": ids[key],
                        "roomId": ids["room"],
                        "ownerId": people["Worker"]["principalId"],
                        "specification": "Produce fixture under current direction",
                        "acceptance": "Independent acceptance against current direction",
                        "dependencies": [],
                    },
                )
            assignment = await assign(ids["work"])
            await post(
                "Worker",
                "connector/claim",
                {
                    "operationId": str(uuid4()),
                    "hostId": ids["host"],
                    "capabilities": ["native-role-goals/v1", "cooperation-plan/v1"],
                },
                409,
            )
            c1 = await claim()
            assert c1["request"]["data"]["attemptId"] == c1["delivery"]["id"]
            b1 = await get("Worker", "connector/deliveries/" + c1["delivery"]["id"] + "/briefing")
            assert b1["attempt"]["number"] == 1
            assert b1["decisions"][0]["body"] == "Version one direction"
            await get(
                "OtherHuman", "connector/deliveries/" + c1["delivery"]["id"] + "/briefing", 403
            )
            await transition(c1, "running")
            await report(c1, b1, b"original-evidence")
            await transition(c1, "completed")

            corrected_source = await post(
                "Owner",
                "rooms/" + ids["room"] + "/messages",
                {
                    "operationId": str(uuid4()),
                    "kind": "message",
                    "body": "Correct the prior decision; preserve original work",
                },
            )
            change = {
                **decision,
                "operationId": str(uuid4()),
                "revision": 1,
                "body": "Version two corrected direction",
                "sourceEventId": corrected_source["event"]["id"],
            }
            second = await post("Owner", "rooms/" + ids["room"] + "/decisions", change)
            await review("accepted", 409)
            await review("rejected")
            checks.append(
                "Completed evidence cannot be accepted after a newer relevant decision; explicit rejection retained"
            )

            current = await work()
            revision = {
                "operationId": str(uuid4()),
                "revision": current["revision"],
                "ownerId": people["Worker"]["principalId"],
                "specification": "Implement corrected direction",
                "acceptance": "Use version two and retain original lineage",
                "note": "Explicit authorized rework",
            }
            await post("Worker", "work/" + ids["work"] + "/revise", revision, 403)
            rework = await post("Owner", "work/" + ids["work"] + "/revise", revision)
            assert rework["deliveryId"] is None
            goal = (await get("Owner", "goals/" + ids["goal"]))["goal"]
            await post(
                "Owner",
                "goals/" + ids["goal"] + "/cooperation",
                {
                    "operationId": str(uuid4()),
                    "revision": goal["revision"],
                    "cooperationRevision": 1,
                    "policy": "kanban_pull",
                    "reviewerId": people["Reviewer"]["principalId"],
                    "executorIds": [people["Worker"]["principalId"]],
                },
                409,
            )
            await assign(ids["work"])
            c2 = await claim()
            assert c2["delivery"]["id"] != c1["delivery"]["id"]
            assert c2["request"]["data"]["attemptNumber"] == 2
            b2 = await get("Worker", "connector/deliveries/" + c2["delivery"]["id"] + "/briefing")
            assert b2["decisions"][0]["revision"] == 2
            assert b2["attempt"]["specification"] == "Implement corrected direction"
            await transition(c2, "running")
            w = await work()
            await post(
                "Worker",
                "work/" + ids["work"] + "/report",
                {
                    "operationId": str(uuid4()),
                    "revision": w["revision"],
                    "claimId": c2["delivery"]["claim_id"],
                    "generation": c2["delivery"]["generation"],
                    "briefingSequence": b1["asOfSequence"],
                    "state": "submitted",
                    "note": "stale",
                    "evidence": [
                        {
                            "hostId": ids["host"],
                            "workspaceId": ids["workspace"],
                            "relativePath": "fixture.txt",
                            "sha256": "0" * 64,
                            "size": 0,
                        }
                    ],
                },
                409,
            )
            await report(c2, b2, b"corrected-evidence")
            await transition(c2, "completed")
            await review("accepted")
            attempts = await get("Reviewer", "work/" + ids["work"] + "/attempts?after=0&limit=1")
            assert len(attempts["attempts"]) == 1 and attempts["nextCursor"] == 1
            prior = attempts["attempts"][0]
            assert prior["review_decision"] == "rejected"
            assert (
                prior["evidence"][0]["sha256"] == hashlib.sha256(b"original-evidence").hexdigest()
            )
            newer = await get("Reviewer", "work/" + ids["work"] + "/attempts?after=1&limit=1")
            assert newer["attempts"][0]["review_decision"] == "accepted"
            checks.append(
                "Rework retains stable work ID, original rejected evidence, distinct attempt and paginated review lineage"
            )

            await assign(ids["unknownWork"])
            unknown = await claim()
            await transition(unknown, "unknown")
            w = (await get("Owner", "work/" + ids["unknownWork"]))["work"]
            await post(
                "Owner",
                "work/" + ids["unknownWork"] + "/revise",
                {**revision, "operationId": str(uuid4()), "revision": w["revision"]},
                409,
            )
            await transition(unknown, "cancelled")
            checks.append(
                "Unknown execution is held rather than reassigned; only explicit fixture stop settles it"
            )

            await runner.cleanup()
            store.close()
            store = Store(json.loads(Path(os.environ["MISSION_DATABASE_CONFIG"]).read_text()))
            await start()
            assert second == await post("Owner", "rooms/" + ids["room"] + "/decisions", change)
            retained = await get("Reviewer", "work/" + ids["work"] + "/attempts")
            assert len(retained["attempts"]) == 2
            supervision = await get("Global", "supervision?after=0&limit=100")
            assert any(
                e["material"] for e in supervision["events"] if e["eventId"] == second["eventId"]
            )
            before = len(trace)
            info = await get("Owner", "rooms/" + ids["room"])
            grant = next(
                m for m in info["memberships"] if m["principal"] == people["Worker"]["principalId"]
            )
            await call(
                "Owner",
                "PUT",
                "rooms/" + ids["room"] + "/membership",
                {
                    "principalId": people["Worker"]["principalId"],
                    "can_read": False,
                    "can_post": False,
                    "can_request": False,
                    "revision": grant["revision"],
                },
            )
            await get("Worker", "rooms/" + ids["room"] + "/decisions", 403)
            await get("Worker", "work/" + ids["work"] + "/attempts", 403)
            await get("Worker", "connector/deliveries/" + c2["delivery"]["id"] + "/briefing", 403)
            checks.append(
                "API/store restart retains original decision receipts and attempts; material supervision and revocation apply"
            )

            # Only owned fixture pending requests; no native executor was launched.
            with store.pool.connection() as db:
                pending = db.execute(
                    "SELECT id FROM hb_delivery WHERE authority=%s AND state='pending'",
                    (ids["authority"],),
                ).fetchall()
            for d in pending:
                await post("Owner", "deliveries/" + str(d["id"]) + "/cancel", {})
            with store.pool.connection() as db:
                unsettled = db.execute(
                    """SELECT count(*) AS n FROM hb_delivery WHERE authority=%s
                       AND state IN ('pending','claimed','running','unknown','cancel_requested')""",
                    (ids["authority"],),
                ).fetchone()["n"]
            assert unsettled == 0
            result = {
                "passed": True,
                "checks": checks,
                "nativeModelStarts": 0,
                "nativeAcknowledgements": "explicit fixtures",
                "unsettledOwnedDeliveries": 0,
                "sourceCase": str(root),
            }
            (root / "result.json").write_text(json.dumps(result, indent=2))
            print(json.dumps(result))
        finally:
            if runner:
                await runner.cleanup()
            store.close()


if __name__ == "__main__":
    asyncio.run(main())
