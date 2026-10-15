"""Real authenticated PostgreSQL admission/portfolio boundaries; zero native dispatches.

Claims are deliberately held before any native runtime exists. Unknown is an
explicit fault injection; cleanup records verified absence of native submission.
"""

import asyncio
import json
import os
import secrets
from pathlib import Path
from uuid import uuid4

from aiohttp import ClientSession, web
from hollywood_backbone.bootstrap import initialize
from hollywood_backbone.server import application
from hollywood_backbone.store import Store
from lax_connector.host import enroll, supervise

CAPS = [
    "native-role-goals/v1",
    "cooperation-plan/v1",
    "work-attempts/v1",
    "room-briefing/v1",
    "host-admission/v1",
    "portfolio-supervision/v1",
]


async def main():
    os.umask(0o077)
    root = Path(os.environ["ADMISSION_EVIDENCE"])
    root.mkdir(mode=0o700)
    config = Path(os.environ["ADMISSION_DATABASE_CONFIG"])
    initialize(
        json.loads(config.read_text()), root / "enterprise", "Admission/portfolio boundaries"
    )
    setup = json.loads((root / "enterprise/installation-intent.json").read_text())
    owner = setup["credentials"]["Owner"]
    glob = setup["credentials"]["Global"]
    people = {"Owner": owner, "Global": glob}
    ids = {k: str(uuid4()) for k in ["host", "red", "blue"]}
    authority, room = setup["authorityId"], setup["globalRoomId"]
    store = Store(json.loads(config.read_text()))
    runner = None
    checks = []
    trace = []
    base = ""

    async def start():
        nonlocal runner, base
        runner = web.AppRunner(application(store))
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", 18774).start()
        base = "http://127.0.0.1:18774"

    await start()
    async with ClientSession() as session:

        async def call(who, method, path, payload=None, expected=200):
            if payload is not None:
                (root / "last-intent.json").write_text(
                    json.dumps({"who": who, "method": method, "path": path, "payload": payload})
                )
            async with session.request(
                method,
                base + "/hollywood/v3/" + path,
                json=payload,
                headers={"Authorization": "Bearer " + people[who]["token"]},
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
                (root / "trace.json").write_text(json.dumps(trace))
                assert response.status == expected, (who, path, response.status, value, expected)
                return value

        async def post(who, path, payload, expected=200):
            return await call(who, "POST", path, payload, expected)

        async def get(who, path, expected=200):
            return await call(who, "GET", path, expected=expected)

        async def observe(name):
            return await post(
                name, "connector/observe", {"hostId": ids["host"], "capabilities": CAPS}
            )

        async def claim(name, operation=None):
            return await post(
                name,
                "connector/claim",
                {
                    "operationId": operation or str(uuid4()),
                    "hostId": ids["host"],
                    "capabilities": CAPS,
                },
            )

        async def message(name, target_room, recipient, operation):
            return await post(
                name,
                "rooms/" + target_room + "/messages",
                {
                    "operationId": operation,
                    "kind": "request",
                    "recipientId": people[recipient]["principalId"],
                    "body": "No native runtime exists in this contract case.",
                },
            )

        async def settle(name, receipt, status):
            d = receipt["delivery"]
            return await post(
                name,
                "deliveries/" + d["id"] + "/transition",
                {
                    "operationId": str(uuid4()),
                    "generation": d["generation"],
                    "claimId": d["claim_id"],
                    "state": status,
                    "body": "Verified pre-native contract cleanup; no runtime or external effect was dispatched.",
                },
            )

        try:
            for name in ["RedA", "RedB", "Blue", "Observer"]:
                people[name] = {"principalId": str(uuid4()), "token": secrets.token_urlsafe(40)}
            (root / "participant-intent.json").write_text(json.dumps(people))
            for name in ["RedA", "RedB", "Blue", "Observer"]:
                await post(
                    "Owner",
                    "principals",
                    {
                        "id": people[name]["principalId"],
                        "name": name,
                        "kind": "human" if name == "Observer" else "agent",
                        "enrollmentCredential": people[name]["token"],
                    },
                )
            await post(
                "Owner",
                "rooms",
                {
                    "id": ids["red"],
                    "name": "Red",
                    "purpose": "Admission red lane",
                    "ownerId": people["RedA"]["principalId"],
                },
            )
            await post(
                "Owner",
                "rooms",
                {
                    "id": ids["blue"],
                    "name": "Blue",
                    "purpose": "Admission blue lane",
                    "ownerId": people["Blue"]["principalId"],
                },
            )
            await call(
                "Owner",
                "PUT",
                "rooms/" + ids["red"] + "/membership",
                {
                    "principalId": people["RedB"]["principalId"],
                    "can_read": True,
                    "can_post": True,
                    "can_request": True,
                    "revision": 0,
                },
            )
            await call(
                "Owner",
                "PUT",
                "rooms/" + room + "/membership",
                {
                    "principalId": people["Observer"]["principalId"],
                    "can_read": True,
                    "can_post": False,
                    "can_request": False,
                    "revision": 0,
                },
            )
            for name in ["Global", "RedA", "RedB", "Blue"]:
                await post(
                    "Owner",
                    "placements",
                    {
                        "hostId": ids["host"],
                        "name": "admission-contract-host",
                        "identityFingerprint": "admission-contract-" + authority,
                        "capabilities": {},
                        "agentId": people[name]["principalId"],
                    },
                )
            enrollment = await asyncio.to_thread(
                enroll,
                {
                    "authorityId": authority,
                    "hostId": ids["host"],
                    "hollywoodUrl": base,
                    "administratorCredential": owner,
                    "policy": {
                        "maxConcurrent": 3,
                        "reservedInteractive": 1,
                        "controlAgentIds": [glob["principalId"]],
                        "memoryPerTurnBytes": 512 * 1024**2,
                        "reserveMemoryBytes": 1024**3,
                        "sampleMaxAgeSeconds": 300,
                        "connectorMaxAgeSeconds": 300,
                    },
                },
            )
            hostconfig = json.loads(Path(enrollment["configPath"]).read_text())
            first = await asyncio.to_thread(supervise, hostconfig, True)
            people["Controller"] = {
                "principalId": enrollment["controllerId"],
                "token": hostconfig["token"],
            }
            original = json.loads((Path(hostconfig["state"]) / "sample-intent.json").read_text())[
                "payload"
            ]
            assert first == await post("Controller", "hosts/" + ids["host"] + "/capacity", original)
            await post(
                "Controller",
                "hosts/" + ids["host"] + "/capacity",
                {**original, "availableMemoryBytes": 0},
                409,
            )
            await post(
                "RedA",
                "hosts/" + ids["host"] + "/capacity",
                {**original, "operationId": str(uuid4())},
                403,
            )
            await post(
                "Controller",
                "connector/claim",
                {"operationId": str(uuid4()), "hostId": ids["host"], "capabilities": CAPS},
                403,
            )
            assert (await get("Controller", "rooms"))["rooms"] == []
            checks.append(
                "Dedicated service controller scopes, original sample receipt and changed-payload conflict"
            )

            request_a = await message("Owner", ids["red"], "RedA", "red-a")
            request_b = await message("Owner", ids["red"], "RedB", "red-b")
            request_blue = await message("Owner", ids["blue"], "Blue", "blue")
            for name in ["RedA", "RedB", "Blue"]:
                await observe(name)
            denied_operation = "original-fair-denial"
            denied = await claim("RedB", denied_operation)
            assert (
                denied["delivery"] is None and denied["admission"]["reason"] == "fair_queue_ahead"
            )
            a = await claim("RedA")
            assert a["delivery"]
            assert (await claim("RedB"))["delivery"] is None
            blue = await claim("Blue")
            assert blue["delivery"]
            control = await message("Owner", room, "Global", "control")
            await observe("Global")
            g = await claim("Global")
            assert g["delivery"]["id"] == control["deliveries"][0]["id"]
            assert g["admission"]["queueClass"] == "interactive"
            await post("Owner", "deliveries/" + g["delivery"]["id"] + "/cancel", {})
            await settle("Global", g, "cancelled")
            await settle("RedA", a, "unknown")
            held = await observe("RedB")
            assert held["reason"] == "worker_capacity_reserved_for_control", held
            await post(
                "RedB",
                "connector/claim",
                {
                    "operationId": str(uuid4()),
                    "hostId": ids["host"],
                    "capabilities": CAPS,
                    "queueClass": "interactive",
                },
                400,
            )
            checks.append(
                "Room fairness, reserved control under full worker reservations, unknown hold and class-forgery denial"
            )

            await runner.cleanup()
            store.close()
            store = Store(json.loads(config.read_text()))
            await start()
            still = await observe("RedB")
            assert still["reason"] == "worker_capacity_reserved_for_control"
            assert (await claim("RedB", denied_operation))["delivery"] is None
            await post("Owner", "deliveries/" + a["delivery"]["id"] + "/cancel", {})
            await settle("RedA", a, "cancelled")
            detail = await get("Owner", "rooms/" + ids["red"])
            grant = next(
                m for m in detail["memberships"] if m["principal"] == people["RedB"]["principalId"]
            )
            await call(
                "Owner",
                "PUT",
                "rooms/" + ids["red"] + "/membership",
                {
                    "principalId": people["RedB"]["principalId"],
                    "can_read": False,
                    "can_post": False,
                    "can_request": False,
                    "revision": grant["revision"],
                },
            )
            hidden = await observe("RedB")
            assert hidden["pending"] is None
            assert request_b["deliveries"][0]["id"] not in json.dumps(hidden)
            await get("RedB", "deliveries/" + request_b["deliveries"][0]["id"], 403)
            checks.append(
                "Restart retains reservation and original nil receipt; revoked-room diagnostics disclose no pending IDs"
            )

            await call(
                "Owner",
                "PUT",
                "agents/" + glob["principalId"] + "/supervision",
                {
                    "operationId": "portfolio",
                    "revision": 0,
                    "roomId": room,
                    "enabled": True,
                    "reviewsPerWindow": 2,
                    "windowSeconds": 3600,
                },
            )
            batch = await message("Owner", room, "Global", "batch-goal-source")
            for n in range(65):
                await post(
                    "Owner",
                    "rooms/" + room + "/goals",
                    {
                        "operationId": "nonready-goal-" + str(n),
                        "id": str(uuid4()),
                        "sourceEventId": batch["event"]["id"],
                        "title": "Nonready " + str(n),
                        "objective": "No admitted work; must not hide later ready incidents.",
                        "acceptance": "No work has been submitted",
                        "reviewBudget": 0,
                    },
                )
            source = await post(
                "Owner",
                "rooms/" + room + "/messages",
                {
                    "operationId": "decision-source",
                    "kind": "message",
                    "body": "One material room decision affects 65 nonready goals.",
                },
            )
            await post(
                "Owner",
                "rooms/" + room + "/decisions",
                {
                    "operationId": "pressure-decision",
                    "id": str(uuid4()),
                    "key": "pressure.direction",
                    "revision": 0,
                    "goalId": None,
                    "state": "active",
                    "body": "Do not dispatch models for unready work.",
                    "sourceEventId": source["event"]["id"],
                },
            )
            await settle("Blue", blue, "unknown")
            review = await post(
                "Global", "connector/supervision", {"hostId": ids["host"], "capabilities": CAPS}
            )
            assert len(review["admitted"]) == 1 and review["admitted"][0]["scope"] == "delivery", (
                review
            )
            repeated = await post(
                "Global", "connector/supervision", {"hostId": ids["host"], "capabilities": CAPS}
            )
            assert repeated["admitted"] == []
            review_id = review["admitted"][0]["deliveryId"]
            await get("Observer", "deliveries/" + review_id, 403)
            public = await get("Observer", "rooms/" + room + "/history?after=0&limit=100")
            assert review_id not in json.dumps(public)
            all_items = []
            cursor = None
            while True:
                suffix = "?limit=20"
                if cursor:
                    suffix += (
                        "&afterReference=" + cursor["reference"] + "&afterScope=" + cursor["scope"]
                    )
                page = await get(
                    "Global", "agents/" + glob["principalId"] + "/supervision" + suffix
                )
                all_items += page["items"]
                cursor = page["nextCursor"]
                if not cursor:
                    break
            assert (
                len(all_items) == 66
                and len({(i["scope"], i["reference"]) for i in all_items}) == 66
            )
            await get("Observer", "agents/" + glob["principalId"] + "/supervision", 403)
            await post(
                "Global",
                "rooms/" + room + "/messages",
                {
                    "operationId": "self-chain",
                    "kind": "request",
                    "recipientId": glob["principalId"],
                    "body": "Unbounded self continuation",
                },
                403,
            )
            await post("Owner", "deliveries/" + review_id + "/cancel", {})
            state = await get("Global", "agents/" + glob["principalId"] + "/supervision?limit=100")
            stopped = next(i for i in state["items"] if i["scope"] == "delivery")
            assert stopped["review_execution_state"] == "cancelled"
            assert (
                await post(
                    "Global", "connector/supervision", {"hostId": ids["host"], "capabilities": CAPS}
                )
            )["admitted"] == []
            checks.append(
                "65 nonready goals cannot starve a ready incident; private single review, complete pagination, stopped receipts and self-chain denial"
            )

            await post("Owner", "deliveries/" + blue["delivery"]["id"] + "/cancel", {})
            await settle("Blue", blue, "cancelled")
            with store.pool.connection() as c:
                rows = c.execute(
                    "SELECT id,state,native_thread,native_turn FROM hb_delivery WHERE authority=%s",
                    (authority,),
                ).fetchall()
            assert all(not d["native_thread"] and not d["native_turn"] for d in rows)
            for d in rows:
                if d["state"] == "pending":
                    await post("Owner", "deliveries/" + str(d["id"]) + "/cancel", {})
            result = {
                "passed": True,
                "checks": checks,
                "nativeModelStarts": 0,
                "proofType": "Actual authenticated API/PostgreSQL plus real host enrollment/resources; pre-native claims and explicit unknown fault injection",
                "allNativePointersAbsent": True,
                "productionChanged": False,
            }
            (root / "result.json").write_text(json.dumps(result, indent=2))
            print(json.dumps(result))
        finally:
            await runner.cleanup()
            store.close()


if __name__ == "__main__":
    asyncio.run(main())
