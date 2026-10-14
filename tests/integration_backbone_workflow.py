"""Actual PostgreSQL + authenticated HTTP lifecycle qualification; no model fixtures claimed as agents."""

import asyncio
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

from aiohttp import ClientSession, web
from hollywood_backbone.server import application
from hollywood_backbone.store import Store

ROOT = Path(os.environ["WORKFLOW_EVIDENCE"])
DB = Path(os.environ["WORKFLOW_DATABASE_CONFIG"])


async def main():
    os.umask(0o077)
    ROOT.mkdir(mode=0o700)
    # This scenario intentionally admits synthetic native receipts, never actual execution.
    # Accepted external fixture mutations are not repeated by restarting this script.
    intent = ROOT / "intent.json"
    if intent.exists():
        raise RuntimeError("Inspect original fixture receipts; do not replay this scenario")
    ids = {
        key: str(uuid4())
        for key in (
            "authority",
            "room",
            "worker",
            "viewer",
            "host",
            "goal",
            "w1",
            "w2",
            "workspace",
            "goal2",
            "w3",
        )
    }
    import secrets

    credentials = {
        label: {"principalId": str(uuid4()), "token": secrets.token_urlsafe(40)}
        for label in ("Owner", "Global")
    }
    plan = {"globalRoomId": ids["room"], "credentials": credentials}
    worker_token = secrets.token_urlsafe(40)
    viewer_token = secrets.token_urlsafe(40)
    (ROOT / "intent.json").write_text(
        json.dumps(
            {"ids": ids, "plan": plan, "workerToken": worker_token, "viewerToken": viewer_token}
        )
    )
    store = Store(json.loads(DB.read_text()))
    store.migrate()
    store.bootstrap(ids["authority"], "Workflow HTTP qualification", plan)
    tokens = {name: row["token"] for name, row in credentials.items()}
    tokens["Worker"] = worker_token
    tokens["Viewer"] = viewer_token
    runner = None
    trace = []
    checks = []

    async def start():
        nonlocal runner
        runner = web.AppRunner(application(store))
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        return (
            "http://127.0.0.1:" + str(site._server.sockets[0].getsockname()[1]) + "/hollywood/v3/"
        )

    base = await start()
    async with ClientSession() as session:

        async def call(who, method, path, payload=None, status=200):
            async with session.request(
                method,
                base + path,
                json=payload,
                headers={"Authorization": "Bearer " + tokens[who]},
            ) as r:
                value = await r.json()
                trace.append(
                    {
                        "who": who,
                        "method": method,
                        "path": path,
                        "status": r.status,
                        "response": value,
                    }
                )
                (ROOT / "http-trace.json").write_text(json.dumps(trace, default=str))
                assert r.status == status, (path, r.status, value)
                return value

        try:
            for name, key, kind in [("Worker", "worker", "agent"), ("Viewer", "viewer", "human")]:
                await call(
                    "Owner",
                    "POST",
                    "principals",
                    {
                        "id": ids[key],
                        "name": name,
                        "kind": kind,
                        "enrollmentCredential": tokens[name],
                    },
                )
            target = str(uuid4())
            ids["workRoom"] = target
            await call(
                "Owner",
                "POST",
                "rooms",
                {
                    "id": target,
                    "name": "Engineering",
                    "purpose": "Owned artifact work",
                    "ownerId": ids["worker"],
                },
            )
            await call(
                "Owner",
                "PUT",
                f"rooms/{target}/membership",
                {
                    "principalId": ids["worker"],
                    "can_read": True,
                    "can_post": True,
                    "can_request": True,
                    "revision": 0,
                },
            )
            for name, aid in [
                ("Global", credentials["Global"]["principalId"]),
                ("Worker", ids["worker"]),
            ]:
                await call(
                    "Owner",
                    "POST",
                    "placements",
                    {
                        "hostId": ids["host"],
                        "name": "Fixture host",
                        "identityFingerprint": hashlib.sha256(ids["host"].encode()).hexdigest(),
                        "capabilities": {"fixtureOnly": True},
                        "agentId": aid,
                    },
                )
            source = await call(
                "Owner",
                "POST",
                f"rooms/{ids['room']}/messages",
                {
                    "operationId": "workflow-source",
                    "kind": "request",
                    "body": "Qualify explicit goals, plans, dependency admission, review, cancellation and recovery using disposable metadata only.",
                    "audience": "room",
                },
            )
            # Settle initial synthetic coordinator turn; this is not native model evidence.
            claim = await call(
                "Global",
                "POST",
                "connector/claim",
                {"operationId": "initial-coordinator-claim", "hostId": ids["host"]},
            )

            async def transition(who, claim, state, op):
                d = claim["delivery"]
                return await call(
                    who,
                    "POST",
                    f"deliveries/{d['id']}/transition",
                    {
                        "operationId": op,
                        "claimId": d["claim_id"],
                        "generation": d["generation"],
                        "state": state,
                        "nativeThread": "fixture-" + d["agent"],
                        "nativeTurn": "fixture-" + d["id"],
                        **(
                            {"body": "Metadata fixture terminal receipt"}
                            if state in ("completed", "cancelled", "failed")
                            else {}
                        ),
                    },
                )

            await transition("Global", claim, "running", "global-initial-running")
            await transition("Global", claim, "completed", "global-initial-complete")
            payload = {
                "operationId": "goal-create",
                "id": ids["goal"],
                "sourceEventId": source["event"]["id"],
                "title": "Verified artifact",
                "objective": "Produce and review two dependent artifacts",
                "acceptance": "Both work items explicitly accepted against evidence",
                "reviewBudget": 1,
            }
            created = await call("Global", "POST", f"rooms/{ids['room']}/goals", payload)
            assert created == await call("Global", "POST", f"rooms/{ids['room']}/goals", payload)
            await call(
                "Global",
                "POST",
                f"rooms/{ids['room']}/goals",
                {**payload, "title": "Changed goal"},
                409,
            )
            await call("Viewer", "GET", f"goals/{ids['goal']}", status=403)
            goal = await call("Global", "GET", f"goals/{ids['goal']}")
            for key, deps in [("w1", []), ("w2", [ids["w1"]])]:
                await call(
                    "Global",
                    "POST",
                    f"goals/{ids['goal']}/work",
                    {
                        "operationId": "plan-" + key,
                        "revision": goal["goal"]["revision"],
                        "id": ids[key],
                        "roomId": target,
                        "ownerId": ids["worker"],
                        "specification": "Metadata fixture " + key,
                        "acceptance": "Independent evidence inspection",
                        "dependencies": deps,
                    },
                )
                goal = await call("Global", "GET", f"goals/{ids['goal']}")
            await call("Worker", "GET", f"goals/{ids['goal']}", status=403)
            await call("Viewer", "GET", f"work/{ids['w1']}", status=403)
            await call(
                "Global",
                "POST",
                f"work/{ids['w2']}/assign",
                {"operationId": "held-dependency", "revision": 1},
                409,
            )
            assert (await call("Worker", "GET", "connector/pending?hostId=" + ids["host"]))[
                "pending"
            ] is None
            checks.append(
                "Dependency plan persisted without execution; unauthorized goal/work reads denied"
            )

            async def assigned(key):
                w = (await call("Global", "GET", f"work/{ids[key]}"))["work"]
                await call(
                    "Global",
                    "POST",
                    f"work/{ids[key]}/assign",
                    {"operationId": "assign-" + key, "revision": w["revision"]},
                )
                c = await call(
                    "Worker",
                    "POST",
                    "connector/claim",
                    {"operationId": "claim-" + key, "hostId": ids["host"]},
                )
                assert c["request"]["data"]["workId"] == ids[key]
                context = await call(
                    "Worker", "GET", f"connector/deliveries/{c['delivery']['id']}/context"
                )
                assert context["goalId"] == ids["goal"] and context["workId"] == ids[key]
                await transition("Worker", c, "running", "running-" + key)
                w = (await call("Worker", "GET", f"work/{ids[key]}"))["work"]
                report = {
                    "operationId": "report-" + key,
                    "revision": w["revision"],
                    "claimId": c["delivery"]["claim_id"],
                    "generation": c["delivery"]["generation"],
                    "state": "submitted",
                    "note": "Fixture evidence declared; not a real artifact.",
                    "evidence": [
                        {
                            "hostId": ids["host"],
                            "workspaceId": ids["workspace"],
                            "relativePath": key + ".txt",
                            "sha256": "0" * 64,
                            "size": 0,
                        }
                    ],
                }
                await call("Worker", "POST", f"work/{ids[key]}/report", report)
                assert (await call("Global", "POST", "connector/reviews", {"hostId": ids["host"]}))[
                    "admitted"
                ] == []
                w = (await call("Global", "GET", f"work/{ids[key]}"))["work"]
                await call(
                    "Global",
                    "POST",
                    f"work/{ids[key]}/review",
                    {
                        "operationId": "premature-" + key,
                        "revision": w["revision"],
                        "decision": "accepted",
                        "note": "Cannot accept a running turn",
                    },
                    409,
                )
                await transition("Worker", c, "completed", "complete-" + key)
                return w, c

            w, c = await assigned("w1")
            # Restart the actual HTTP server, retaining PG ownership and receipts.
            await runner.cleanup()
            base = await start()
            original = await call("Global", "GET", "commands/assign-w1")
            assert original["workflow"]["deliveryId"] == c["delivery"]["id"]
            dispatch = await call("Global", "POST", "connector/reviews", {"hostId": ids["host"]})
            assert len(dispatch["admitted"]) == 1
            assert (await call("Global", "POST", "connector/reviews", {"hostId": ids["host"]}))[
                "admitted"
            ] == []
            reviewclaim = await call(
                "Global",
                "POST",
                "connector/claim",
                {"operationId": "review-claim", "hostId": ids["host"]},
            )
            assert reviewclaim["request"]["data"]["goalReview"] is True
            await call(
                "Worker",
                "POST",
                f"work/{ids['w1']}/review",
                {
                    "operationId": "self-review",
                    "revision": w["revision"],
                    "decision": "accepted",
                    "note": "No self acceptance",
                },
                403,
            )
            await call(
                "Global",
                "POST",
                f"work/{ids['w1']}/review",
                {
                    "operationId": "review-w1",
                    "revision": w["revision"],
                    "decision": "accepted",
                    "note": "Fixture explicitly accepted",
                },
            )
            await transition("Global", reviewclaim, "running", "review-running")
            await transition("Global", reviewclaim, "completed", "review-completed")
            checks.append(
                "Worker submission, native completion and business acceptance remain separate across actual API restart"
            )
            checks.append(
                "Explicit review policy coalesces and spends one bounded review; repeated reconciliation does not duplicate it"
            )
            w, c = await assigned("w2")
            await call(
                "Global",
                "POST",
                f"work/{ids['w2']}/review",
                {
                    "operationId": "review-w2",
                    "revision": w["revision"],
                    "decision": "accepted",
                    "note": "Fixture accepted manually after dependency",
                },
            )
            goal = await call("Global", "GET", f"goals/{ids['goal']}")
            await call(
                "Global",
                "POST",
                f"goals/{ids['goal']}/state",
                {
                    "operationId": "goal-accept",
                    "revision": goal["goal"]["revision"],
                    "state": "accepted",
                    "note": "Both criteria reviewed explicitly",
                },
            )
            assert (await call("Global", "POST", "connector/reviews", {"hostId": ids["host"]}))[
                "admitted"
            ] == []
            checks.append(
                "Accepted prerequisite allows dependent admission; accepted work permits explicit goal acceptance"
            )
            g2 = await call(
                "Global",
                "POST",
                f"rooms/{ids['room']}/goals",
                {**payload, "operationId": "goal2-create", "id": ids["goal2"], "reviewBudget": 0},
            )
            await call(
                "Global",
                "POST",
                f"goals/{ids['goal2']}/work",
                {
                    "operationId": "plan-w3",
                    "revision": g2["goalRevision"],
                    "id": ids["w3"],
                    "roomId": target,
                    "ownerId": ids["worker"],
                    "specification": "Pause/cancel fixture",
                    "acceptance": "No uncontrolled execution",
                    "dependencies": [],
                },
            )
            await call(
                "Global",
                "POST",
                f"work/{ids['w3']}/assign",
                {"operationId": "assign-w3", "revision": 1},
            )
            g = await call("Global", "GET", f"goals/{ids['goal2']}")
            await call(
                "Global",
                "POST",
                f"goals/{ids['goal2']}/state",
                {
                    "operationId": "pause-goal2",
                    "revision": g["goal"]["revision"],
                    "state": "paused",
                    "note": "Explicit pause fixture",
                },
            )
            assert (
                await call(
                    "Worker",
                    "POST",
                    "connector/claim",
                    {"operationId": "paused-claim", "hostId": ids["host"]},
                )
            )["delivery"] is None
            g = await call("Global", "GET", f"goals/{ids['goal2']}")
            await call(
                "Global",
                "POST",
                f"goals/{ids['goal2']}/state",
                {
                    "operationId": "resume-goal2",
                    "revision": g["goal"]["revision"],
                    "state": "active",
                    "note": "Explicit resume fixture",
                },
            )
            c = await call(
                "Worker",
                "POST",
                "connector/claim",
                {"operationId": "resumed-claim", "hostId": ids["host"]},
            )
            await transition("Worker", c, "running", "w3-running")
            w = (await call("Global", "GET", f"work/{ids['w3']}"))["work"]
            cancelled = await call(
                "Global",
                "POST",
                f"work/{ids['w3']}/cancel",
                {
                    "operationId": "cancel-w3",
                    "revision": w["revision"],
                    "note": "Explicit cancellation fixture",
                },
            )
            assert cancelled["state"] == "cancel_requested"
            g = await call("Global", "GET", f"goals/{ids['goal2']}")
            await call(
                "Global",
                "POST",
                f"goals/{ids['goal2']}/state",
                {
                    "operationId": "premature-cancel-goal2",
                    "revision": g["goal"]["revision"],
                    "state": "cancelled",
                    "note": "Cannot pretend running effects stopped",
                },
                409,
            )
            await transition("Worker", c, "cancelled", "w3-cancel-ack")
            g = await call("Global", "GET", f"goals/{ids['goal2']}")
            await call(
                "Global",
                "POST",
                f"goals/{ids['goal2']}/state",
                {
                    "operationId": "cancel-goal2",
                    "revision": g["goal"]["revision"],
                    "state": "cancelled",
                    "note": "Native stop receipt retained",
                },
            )
            checks.append(
                "Pause prevents new claims; explicit resume admits original work; cancellation request is distinct from acknowledgement"
            )
            page = await call("Global", "GET", "supervision?after=0&limit=100")
            assert any(e["material"] and e["workId"] == ids["w1"] for e in page["events"])
            ack = await call(
                "Global",
                "PUT",
                "supervision/cursor",
                {"sequence": page["nextCursor"], "revision": page["cursorRevision"]},
            )
            await runner.cleanup()
            base = await start()
            recovered = await call("Global", "GET", "supervision")
            assert recovered["cursorRevision"] == ack["revision"] and all(
                e["sequence"] > ack["seq"] for e in recovered["events"]
            )
            assert (await call("Viewer", "GET", "supervision"))["events"] == []
            checks.append(
                "Global material-activity feed and cursor survive API restart; ungranted human sees no room activity"
            )
            # Revoking the parent room removes retained goal management.
            for room in (ids["room"], target):
                await call(
                    "Owner",
                    "PUT",
                    f"rooms/{room}/membership",
                    {
                        "principalId": ids["viewer"],
                        "can_read": True,
                        "can_post": True,
                        "can_request": True,
                        "revision": 0,
                    },
                )
            goal3 = str(uuid4())
            w4 = str(uuid4())
            with (ROOT / "revocation-intent.json").open("w") as f:
                json.dump({"goalId": goal3, "workId": w4}, f)
            g = await call(
                "Viewer",
                "POST",
                f"rooms/{ids['room']}/goals",
                {**payload, "operationId": "viewer-goal", "id": goal3, "reviewBudget": 0},
            )
            await call(
                "Viewer",
                "POST",
                f"goals/{goal3}/work",
                {
                    "operationId": "viewer-plan",
                    "revision": g["goalRevision"],
                    "id": w4,
                    "roomId": target,
                    "ownerId": ids["worker"],
                    "specification": "Revocation fixture",
                    "acceptance": "Must not execute after creator loses goal access",
                    "dependencies": [],
                },
            )
            await call(
                "Owner",
                "PUT",
                f"rooms/{ids['room']}/membership",
                {
                    "principalId": ids["viewer"],
                    "can_read": False,
                    "can_post": False,
                    "can_request": False,
                    "revision": 1,
                },
            )
            await call("Viewer", "GET", f"work/{w4}")
            await call(
                "Viewer",
                "POST",
                f"work/{w4}/assign",
                {"operationId": "revoked-assign", "revision": 1},
                403,
            )
            checks.append(
                "Revoked goal-room access denies retained management despite remaining work-room access"
            )
            result = {
                "state": "passed",
                "checks": checks,
                "authorityId": ids["authority"],
                "goalId": ids["goal"],
                "actualPostgres": True,
                "authenticatedHttp": True,
                "nativeReceipts": "synthetic fixtures only",
                "modelsStarted": 0,
                "physicalPhone": False,
                "productionChanged": False,
            }
            (ROOT / "result.json").write_text(json.dumps(result, indent=2))
            print(json.dumps(result))
        finally:
            if runner:
                await runner.cleanup()
            store.close()


if __name__ == "__main__":
    asyncio.run(main())
