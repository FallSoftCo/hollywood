"""Real authenticated HTTP/PostgreSQL peer cooperation; native acks here are explicit fixtures."""

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


async def main():
    os.umask(0o077)
    root = Path(os.environ["COOPERATION_EVIDENCE"])
    root.mkdir(mode=0o700)
    db = json.loads(Path(os.environ["COOPERATION_DATABASE_CONFIG"]).read_text())
    ids = {
        k: str(uuid4())
        for k in ["authority", "room", "host", "workspace", "g1", "g2", "w1", "w2", "direct"]
    }
    principals = {
        k: {"principalId": str(uuid4()), "token": secrets.token_urlsafe(40)}
        for k in [
            "Owner",
            "Global",
            "OtherHuman",
            "PlannerA",
            "PlannerB",
            "ExecutorA",
            "ExecutorB",
            "Verifier",
            "Observer",
        ]
    }
    intent = {"ids": ids, "credentials": principals, "globalRoomId": ids["room"]}
    (root / "intent.json").write_text(json.dumps(intent))
    store = Store(db)
    store.migrate()
    store.bootstrap(
        ids["authority"],
        "Independent peer cooperation qualification",
        {**intent, "credentials": {k: principals[k] for k in ["Owner", "Global"]}},
    )
    runner = None
    base = ""
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
                headers={"Authorization": "Bearer " + principals[who]["token"]},
            ) as response:
                value = await response.json()
                trace.append(
                    {
                        "who": who,
                        "method": method,
                        "path": path,
                        "status": response.status,
                        "value": value,
                    }
                )
                (root / "trace.json").write_text(json.dumps(trace, default=str))
                allowed = expected if isinstance(expected, tuple) else (expected,)
                assert response.status in allowed, (who, path, response.status, value)
                return value, response.status

        async def post(who, path, payload, expected=200):
            return (await call(who, "POST", path, payload, expected))[0]

        async def get(who, path):
            return (await call(who, "GET", path))[0]

        async def state(who, goal, new, expected=200):
            current = await get(who, "goals/" + ids[goal])
            return await post(
                who,
                "goals/" + ids[goal] + "/state",
                {
                    "operationId": str(uuid4()),
                    "revision": current["goal"]["revision"],
                    "state": new,
                    "note": "Scoped integration control",
                },
                expected,
            )

        async def new_work(goal, key, owner, deps=()):
            current = await get("Owner", "goals/" + ids[goal])
            return await post(
                "PlannerA" if goal == "g1" else "PlannerB",
                "goals/" + ids[goal] + "/work",
                {
                    "operationId": str(uuid4()),
                    "revision": current["goal"]["revision"],
                    "id": ids[key],
                    "roomId": ids["room"],
                    "ownerId": principals[owner]["principalId"] if owner else None,
                    "specification": "Produce the bounded cooperation artifact " + key,
                    "acceptance": "Independent artifact review; native completion is separate",
                    "dependencies": [ids[d] for d in deps],
                },
            )

        async def transition(who, delivery, claim, status):
            return await post(
                who,
                "deliveries/" + delivery + "/transition",
                {
                    "operationId": str(uuid4()),
                    "claimId": claim,
                    "generation": 1,
                    "state": status,
                    "nativeThread": "fixture-review-thread"
                    if who == "Verifier"
                    else "fixture-thread-" + who,
                    "nativeTurn": "fixture-turn-" + who,
                    "body": "Explicit HTTP fixture acknowledgement",
                },
            )

        caps = ["native-role-goals/v1", "cooperation-plan/v1"]
        try:
            for name in principals:
                if name in ("Owner", "Global"):
                    continue
                await post(
                    "Owner",
                    "principals",
                    {
                        "id": principals[name]["principalId"],
                        "name": name,
                        "kind": "human" if name == "OtherHuman" else "agent",
                        "enrollmentCredential": principals[name]["token"],
                    },
                )
                await call(
                    "Owner",
                    "PUT",
                    "rooms/" + ids["room"] + "/membership",
                    {
                        "principalId": principals[name]["principalId"],
                        "can_read": True,
                        "can_post": True,
                        "can_request": name != "Observer",
                        "revision": 0,
                    },
                )
                if name != "OtherHuman":
                    await post(
                        "Owner",
                        "placements",
                        {
                            "hostId": ids["host"],
                            "name": "cooperation-fixture-host",
                            "identityFingerprint": "cooperation-fixture-owned-host",
                            "capabilities": {},
                            "agentId": principals[name]["principalId"],
                        },
                    )
            for human, agent, goal in [
                ("Owner", "PlannerA", "g1"),
                ("OtherHuman", "PlannerB", "g2"),
            ]:
                request = await post(
                    human,
                    "rooms/" + ids["room"] + "/messages",
                    {
                        "operationId": str(uuid4()),
                        "kind": "request",
                        "body": "Coordinate my bounded work independently",
                        "recipientId": principals[agent]["principalId"],
                    },
                )
                created = await post(
                    agent,
                    "rooms/" + ids["room"] + "/goals",
                    {
                        "operationId": str(uuid4()),
                        "id": ids[goal],
                        "sourceEventId": request["event"]["id"],
                        "title": goal,
                        "objective": "Independent room peer cooperation",
                        "acceptance": "Reviewed work",
                        "reviewBudget": 1,
                    },
                )
                current = await get(agent, "goals/" + ids[goal])
                assert current["goal"]["owner"] == principals[agent]["principalId"]
                assert current["goal"]["source"] == request["event"]["id"]
                if goal == "g1":
                    await post(
                        "PlannerB",
                        "rooms/" + ids["room"] + "/goals",
                        {
                            "operationId": str(uuid4()),
                            "id": str(uuid4()),
                            "sourceEventId": request["event"]["id"],
                            "title": "forged",
                            "objective": "Hijack another addressed exchange",
                            "acceptance": "Denied",
                            "reviewBudget": 0,
                        },
                        403,
                    )
                config = {
                    "operationId": str(uuid4()),
                    "revision": created["goalRevision"],
                    "cooperationRevision": 0,
                    "policy": "leader_award" if goal == "g1" else "kanban_pull",
                    "reviewerId": principals["Verifier"]["principalId"],
                    "executorIds": [
                        principals[x]["principalId"] for x in ["ExecutorA", "ExecutorB"]
                    ],
                }
                configured = await post(agent, "goals/" + ids[goal] + "/cooperation", config)
                replay = await post(agent, "goals/" + ids[goal] + "/cooperation", config)
                assert configured == replay
                await post(
                    agent,
                    "goals/" + ids[goal] + "/cooperation",
                    {**config, "policy": "kanban_pull" if goal == "g1" else "leader_award"},
                    409,
                )
            checks.append(
                "Two distinct humans address different independent peers in the same room; room default does not take ownership"
            )
            checks.append(
                "Goal creation belongs to the addressed exchange; cooperation CAS/replay/conflicting reuse qualified"
            )
            await new_work("g2", "w1", None)
            await new_work("g2", "w2", None, ["w1"])
            await post(
                "ExecutorA",
                "work/" + ids["w2"] + "/claim",
                {"operationId": str(uuid4()), "revision": 1},
                409,
            )
            await post(
                "Observer",
                "work/" + ids["w1"] + "/claim",
                {"operationId": str(uuid4()), "revision": 1},
                403,
            )
            await post(
                "Verifier",
                "work/" + ids["w1"] + "/claim",
                {"operationId": str(uuid4()), "revision": 1},
                403,
            )
            operations = {n: str(uuid4()) for n in ["ExecutorA", "ExecutorB"]}
            raced = await asyncio.gather(
                *(
                    call(
                        n,
                        "POST",
                        "work/" + ids["w1"] + "/claim",
                        {"operationId": operations[n], "revision": 1},
                        (200, 403, 409),
                    )
                    for n in operations
                )
            )
            winners = [n for n, r in zip(operations, raced, strict=True) if r[1] == 200]
            assert len(winners) == 1
            winner = winners[0]
            loser = next(n for n in operations if n != winner)
            accepted = raced[list(operations).index(winner)][0]
            repeated = await post(
                winner,
                "work/" + ids["w1"] + "/claim",
                {"operationId": operations[winner], "revision": 1},
            )
            assert repeated == accepted
            checks.append(
                "Competing authorized Kanban claims admit one owner/delivery; dependent, observer and verifier claims denied"
            )
            await post(
                winner,
                "connector/claim",
                {
                    "operationId": "old-capability",
                    "hostId": ids["host"],
                    "capabilities": ["native-role-goals/v1"],
                },
                409,
            )
            claim = await post(
                winner,
                "connector/claim",
                {"operationId": "native-fixture", "hostId": ids["host"], "capabilities": caps},
            )
            delivery = claim["delivery"]["id"]
            generation = claim["delivery"]["generation"]
            assert generation == 1 and delivery == accepted["deliveryId"]
            context = await get(winner, "connector/deliveries/" + delivery + "/context")
            assert context["cooperation"]["role"] == "executor"
            await transition(winner, delivery, "native-fixture", "running")
            evidence = [
                {
                    "hostId": ids["host"],
                    "workspaceId": ids["workspace"],
                    "relativePath": "fixture.txt",
                    "sha256": hashlib.sha256(b"EXPLICIT_HTTP_FIXTURE\n").hexdigest(),
                    "size": 22,
                }
            ]
            await post(
                winner,
                "work/" + ids["w1"] + "/report",
                {
                    "operationId": "report",
                    "revision": 2,
                    "claimId": "native-fixture",
                    "generation": 1,
                    "state": "submitted",
                    "note": "Synthetic native receipt fixture; not real agent execution",
                    "evidence": evidence,
                },
            )
            for actor, expected in [("Verifier", 409), ("PlannerB", 403), (winner, 403)]:
                await post(
                    actor,
                    "work/" + ids["w1"] + "/review",
                    {
                        "operationId": str(uuid4()),
                        "revision": 3,
                        "decision": "accepted",
                        "note": "Fixture review",
                    },
                    expected,
                )
            await transition(winner, delivery, "native-fixture", "completed")
            review = await post(
                "Verifier", "connector/reviews", {"hostId": ids["host"], "capabilities": caps}
            )
            assert len(review["admitted"]) == 1 and review["admitted"][0]["goalId"] == ids["g2"]
            assert not (
                await post(
                    "PlannerB", "connector/reviews", {"hostId": ids["host"], "capabilities": caps}
                )
            )["admitted"]
            review_claim = await post(
                "Verifier",
                "connector/claim",
                {"operationId": "review-fixture", "hostId": ids["host"], "capabilities": caps},
            )
            rd = review_claim["delivery"]["id"]
            rc = await get("Verifier", "connector/deliveries/" + rd + "/context")
            assert rc["cooperation"]["role"] == "verifier" and rc["goalReview"]
            await transition("Verifier", rd, "review-fixture", "running")
            await post(
                "Verifier",
                "connector/deliveries/" + rd + "/goal-binding",
                {
                    "claimId": "review-fixture",
                    "generation": 1,
                    "goalId": ids["g2"],
                    "workId": None,
                    "nativeThread": "fixture-review-thread",
                    "nativeGoalId": "fixture-review-goal",
                    "contextId": "b" * 64,
                    "workspaceId": ids["workspace"],
                    "objectiveDigest": "a" * 64,
                    "bindingKind": "coordination",
                    "roleRevision": None,
                },
            )
            await post(
                "Verifier",
                "work/" + ids["w1"] + "/review",
                {
                    "operationId": "accept",
                    "revision": 3,
                    "decision": "accepted",
                    "note": "Explicit independent HTTP fixture acceptance",
                },
            )
            current = await get("Verifier", "goals/" + ids["g2"])
            await post(
                "Verifier",
                "goals/" + ids["g2"] + "/work",
                {
                    "operationId": str(uuid4()),
                    "revision": current["goal"]["revision"],
                    "id": str(uuid4()),
                    "roomId": ids["room"],
                    "ownerId": principals["ExecutorA"]["principalId"],
                    "specification": "Verifier must not plan",
                    "acceptance": "Denied",
                    "dependencies": [],
                },
                403,
            )
            checks.append(
                "Independent verifier receives the bounded material review and a scoped native binding; planner/self acceptance and verifier replanning denied"
            )
            await state("PlannerB", "g2", "paused")
            await post(
                loser,
                "work/" + ids["w2"] + "/claim",
                {"operationId": str(uuid4()), "revision": 1},
                409,
            )
            await state("PlannerB", "g2", "active")
            second = await post(
                loser, "work/" + ids["w2"] + "/claim", {"operationId": str(uuid4()), "revision": 1}
            )
            assert second["deliveryId"] != delivery
            cfg = {
                **config,
                "operationId": str(uuid4()),
                "revision": (await get("PlannerB", "goals/" + ids["g2"]))["goal"]["revision"],
                "cooperationRevision": 1,
            }
            await post("PlannerB", "goals/" + ids["g2"] + "/cooperation", cfg, 409)
            checks.append(
                "Accepted prerequisites unblock a distinct peer; pause gates claims and live policy replacement is held"
            )
            await new_work("g1", "direct", "ExecutorA")
            direct = await post(
                "PlannerA",
                "work/" + ids["direct"] + "/assign",
                {"operationId": "direct-assignment", "revision": 1},
            )
            assert direct["deliveryId"]
            await state("PlannerA", "g1", "cancelled")
            await state("PlannerB", "g2", "cancelled")
            await transition("Verifier", rd, "review-fixture", "completed")
            await runner.cleanup()
            runner = None
            store.close()
            store = Store(db)
            await start()
            recovered = await post(
                winner,
                "work/" + ids["w1"] + "/claim",
                {"operationId": operations[winner], "revision": 1},
            )
            assert recovered == accepted
            assert (await get("Owner", "work/" + ids["w1"]))["work"]["state"] == "accepted"
            assert (await get("Owner", "work/" + ids["w2"]))["work"]["state"] == "cancelled"
            checks.append(
                "API restart preserves original claim/receipt and accepted work; cancellation does not reopen or replay it"
            )
            (root / "result.json").write_text(
                json.dumps(
                    {
                        "passed": True,
                        "checks": checks,
                        "nativeExecution": "synthetic fixture acknowledgements only",
                        "productionChanged": False,
                        "schema": 8,
                        "ids": ids,
                        "winner": winner,
                    },
                    indent=2,
                )
            )
            print(
                json.dumps(
                    {
                        "passed": True,
                        "checks": len(checks),
                        "nativeModels": 0,
                        "productionChanged": False,
                    }
                )
            )
        finally:
            if runner:
                await runner.cleanup()
            store.close()


if __name__ == "__main__":
    asyncio.run(main())
