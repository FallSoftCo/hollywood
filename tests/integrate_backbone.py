"""End-to-end HTTP/PostgreSQL scenario; no mock database and no model claims.

Run against a disposable authority. Tokens and full receipts remain private.
Native model execution and physical phone qualification are separate gates.
"""

import argparse
import asyncio
import hashlib
import json
import os
import secrets
import subprocess
import uuid
from pathlib import Path

from aiohttp import ClientSession


def identity():
    return str(uuid.uuid4())


async def main(args):
    os.umask(0o077)
    root = args.state
    install = json.loads((root / "installation-intent.json").read_text())
    credentials = install["credentials"]
    owner = credentials["Owner"]["token"]
    global_agent = credentials["Global"]["principalId"]
    global_token = credentials["Global"]["token"]
    global_room = install["globalRoomId"]
    journal = root / "integration-intent.json"
    if journal.exists():
        raise RuntimeError(
            "Original integration intent exists; inspect retained receipts before any replay"
        )
    run = {
        "engineering": identity(),
        "marketing": identity(),
        "alice": identity(),
        "bob": identity(),
        "room": identity(),
        "host": identity(),
        "foreignHost": identity(),
    }
    journal.write_text(json.dumps(run, indent=2))
    checks = []
    receipts = []
    async with ClientSession() as session:

        async def call(token, method, path, data=None, expected=200):
            async with session.request(
                method, args.url + path, headers={"Authorization": "Bearer " + token}, json=data
            ) as response:
                result = await response.json()
                receipts.append(
                    {"method": method, "path": path, "status": response.status, "result": result}
                )
                (root / "integration-private-receipts.json").write_text(
                    json.dumps(receipts, indent=2)
                )
                assert response.status == expected, (
                    f"{method} {path}: expected {expected}, observed {response.status}"
                )
                return result

        prefix = "/hollywood/v3"
        keys = {}
        for name in ["engineering", "marketing", "alice", "bob"]:
            keys[name] = secrets.token_urlsafe(40)
            (root / "integration-private-credentials.json").write_text(json.dumps(keys))
            request = {
                "id": run[name],
                "name": name,
                "kind": "agent" if name in ["engineering", "marketing"] else "human",
                "enrollmentCredential": keys[name],
            }
            result = await call(owner, "POST", prefix + "/principals", request)
            assert "token" not in result
            repeated = await call(owner, "POST", prefix + "/principals", request)
            assert repeated["principalId"] == result["principalId"] and not repeated["created"]
        (root / "integration-private-credentials.json").write_text(json.dumps(keys))
        await call(
            owner,
            "POST",
            prefix + "/rooms",
            {
                "id": run["room"],
                "name": "Engineering",
                "purpose": "Bounded deliverable integration",
                "ownerId": run["engineering"],
            },
        )

        async def grant(room, name, read, post, request, revision=0):
            return await call(
                owner,
                "PUT",
                prefix + "/rooms/" + room + "/membership",
                {
                    "principalId": run[name],
                    "can_read": read,
                    "can_post": post,
                    "can_request": request,
                    "revision": revision,
                },
            )

        await grant(global_room, "marketing", True, False, False)
        await grant(run["room"], "alice", True, True, True)
        await grant(run["room"], "bob", True, False, False)

        def payload(operation, body, kind="request", **extra):
            return {
                "operationId": operation,
                "kind": kind,
                "body": body,
                "audience": "room",
                **extra,
            }

        global_request = await call(
            owner,
            "POST",
            prefix + "/rooms/" + global_room + "/messages",
            payload("global-request", "Integration request for Global"),
        )
        assert len(global_request["deliveries"]) == 1
        assert global_request["deliveries"][0]["agent"] == global_agent
        observed = await call(
            keys["marketing"], "GET", prefix + "/rooms/" + global_room + "/history"
        )
        assert global_request["event"]["id"] in [e["id"] for e in observed["events"]]
        checks.append(
            "Global request reaches Global alone; room observer sees communication without execution"
        )
        message = await call(
            owner,
            "POST",
            prefix + "/rooms/" + global_room + "/messages",
            payload("observation", "Room publication", "message"),
        )
        assert message["deliveries"] == []
        checks.append("Publication does not create an execution delivery")

        for agent in [global_agent, run["engineering"]]:
            await call(
                owner,
                "POST",
                prefix + "/placements",
                {
                    "hostId": run["host"],
                    "name": "OZZZ integration host",
                    "identityFingerprint": hashlib.sha256(
                        Path("/etc/machine-id").read_bytes()
                    ).hexdigest(),
                    "capabilities": {"qualification": "HTTP/PostgreSQL state only"},
                    "agentId": agent,
                },
            )
        claim = await call(
            global_token,
            "POST",
            prefix + "/connector/claim",
            {"operationId": "global-claim", "hostId": run["host"]},
        )
        assert claim["request"]["id"] == global_request["event"]["id"]
        again = await call(
            global_token,
            "POST",
            prefix + "/connector/claim",
            {"operationId": "global-claim", "hostId": run["host"]},
        )
        assert again == claim
        await call(
            keys["marketing"],
            "POST",
            prefix + "/connector/claim",
            {"operationId": "bad-host", "hostId": run["host"]},
            expected=403,
        )
        checks.append(
            "Enrolled connector alone claims work; lost claim reply reconciles original ownership"
        )

        public = payload("engineering-request", "Direct room-owner work")
        request = await call(
            keys["alice"], "POST", prefix + "/rooms/" + run["room"] + "/messages", public
        )
        assert [d["agent"] for d in request["deliveries"]] == [run["engineering"]]
        duplicate = await call(
            keys["alice"], "POST", prefix + "/rooms/" + run["room"] + "/messages", public
        )
        assert duplicate == request
        await call(
            keys["alice"],
            "POST",
            prefix + "/rooms/" + run["room"] + "/messages",
            {**public, "body": "Changed"},
            expected=409,
        )
        await call(keys["bob"], "GET", prefix + "/commands/engineering-request", expected=403)
        await call(
            keys["bob"],
            "POST",
            prefix + "/rooms/" + run["room"] + "/messages",
            public,
            expected=403,
        )
        checks.append(
            "Owner routing, same-ID dedup, changed-payload conflict and principal-scoped receipts"
        )

        private = await call(
            keys["alice"],
            "POST",
            prefix + "/rooms/" + run["room"] + "/messages",
            payload(
                "private",
                "Private engineering context",
                "message",
                audience="direct",
                recipientId=run["engineering"],
            ),
        )
        bob_history = await call(keys["bob"], "GET", prefix + "/rooms/" + run["room"] + "/history")
        assert private["event"]["id"] not in [e["id"] for e in bob_history["events"]]
        await call(
            keys["bob"],
            "POST",
            prefix + "/rooms/" + global_room + "/messages",
            public,
            expected=403,
        )
        checks.append("Private history and foreign-room access are audience filtered")

        await asyncio.gather(
            *[
                call(
                    owner,
                    "POST",
                    prefix + "/rooms/" + run["room"] + "/messages",
                    payload("parallel-" + str(n), "Concurrent publication " + str(n), "message"),
                )
                for n in range(16)
            ]
        )
        history = await call(owner, "GET", prefix + "/rooms/" + run["room"] + "/history?limit=3")
        events = history["events"][:]
        cursor = history["nextCursor"]
        while True:
            page = await call(
                owner,
                "GET",
                prefix + "/rooms/" + run["room"] + "/history?limit=3&after=" + str(cursor),
            )
            events.extend(page["events"])
            cursor = page["nextCursor"]
            if len(page["events"]) < 3:
                break
        assert len([e for e in events if e["body"].startswith("Concurrent publication")]) == 16
        assert len({e["id"] for e in events}) == len(events)
        assert [e["seq"] for e in events] == sorted(e["seq"] for e in events)
        checks.append("Concurrent committed events replay without loss or duplicate pages")

        claims = await asyncio.gather(
            *[
                call(
                    keys["engineering"],
                    "POST",
                    prefix + "/connector/claim",
                    {"operationId": "worker-claim-" + str(n), "hostId": run["host"]},
                )
                for n in range(2)
            ]
        )
        admitted = [v for v in claims if v["delivery"]]
        assert len(admitted) == 1
        delivery = admitted[0]["delivery"]
        await call(
            keys["engineering"],
            "POST",
            prefix + "/deliveries/" + delivery["id"] + "/transition",
            {
                "operationId": "stale",
                "claimId": delivery["claim_id"],
                "generation": delivery["generation"] + 1,
                "state": "running",
                "nativeTurn": "simulated-turn",
            },
            expected=409,
        )
        cancelled = await call(
            keys["alice"], "POST", prefix + "/deliveries/" + delivery["id"] + "/cancel", {}
        )
        assert cancelled["delivery"]["state"] == "cancel_requested"
        terminal = {
            "operationId": "stop-ack",
            "claimId": delivery["claim_id"],
            "generation": delivery["generation"],
            "state": "cancelled",
            "body": "Protocol fixture acknowledges stopped execution; no native model was run",
        }
        done = await call(
            keys["engineering"],
            "POST",
            prefix + "/deliveries/" + delivery["id"] + "/transition",
            terminal,
        )
        duplicate = await call(
            keys["engineering"],
            "POST",
            prefix + "/deliveries/" + delivery["id"] + "/transition",
            terminal,
        )
        assert done == duplicate and done["delivery"]["result_event"]
        checks.append(
            "Competing connectors admit one owner; stale generation rejected; cancellation request and acknowledgement distinct"
        )

        pending = await call(
            keys["alice"],
            "POST",
            prefix + "/rooms/" + run["room"] + "/messages",
            payload("cancel-pending", "Never admit this work"),
        )
        stopped = await call(
            keys["alice"],
            "POST",
            prefix + "/deliveries/" + pending["deliveries"][0]["id"] + "/cancel",
            {},
        )
        assert stopped["delivery"]["state"] == "cancelled"
        unknown = await call(
            keys["alice"],
            "POST",
            prefix + "/rooms/" + run["room"] + "/messages",
            payload("unknown-request", "Lost execution reply fixture"),
        )
        admitted = await call(
            keys["engineering"],
            "POST",
            prefix + "/connector/claim",
            {"operationId": "unknown-claim", "hostId": run["host"]},
        )
        d = admitted["delivery"]
        assert d["event"] == unknown["event"]["id"]
        await call(
            keys["engineering"],
            "POST",
            prefix + "/deliveries/" + d["id"] + "/transition",
            {
                "operationId": "unknown-state",
                "claimId": d["claim_id"],
                "generation": d["generation"],
                "state": "unknown",
            },
        )
        checks.append("Unsubmitted cancellation is terminal; admitted unknown effects remain held")

        await call(
            keys["alice"],
            "POST",
            prefix + "/rooms/" + run["room"] + "/messages",
            payload("behind-unknown", "Must remain queued"),
        )
        blocked = await call(
            keys["engineering"],
            "POST",
            prefix + "/connector/claim",
            {"operationId": "no-double-owner", "hostId": run["host"]},
        )
        assert blocked["delivery"] is None
        # Restart ONLY the named disposable candidate, never the production service.
        subprocess.run(["systemctl", "--user", "restart", args.unit], check=True)
        for n in range(40):
            try:
                async with session.get(args.url + "/health") as response:
                    if response.status == 200:
                        break
            except OSError:
                pass
            await asyncio.sleep(0.25)
        original = await call(keys["alice"], "GET", prefix + "/commands/unknown-request")
        assert original["deliveries"][0]["state"] == "unknown"
        original_claim = await call(
            keys["engineering"],
            "POST",
            prefix + "/connector/claim",
            {"operationId": "unknown-claim", "hostId": run["host"]},
        )
        assert (
            original_claim["delivery"]["id"] == d["id"]
            and original_claim["delivery"]["state"] == "unknown"
        )
        checks.append(
            "Real API service restart retains unknown delivery and original connector claim; no automatic replay"
        )

        await grant(run["room"], "alice", False, False, False, revision=1)
        await call(keys["alice"], "GET", prefix + "/commands/unknown-request", expected=403)
        await call(
            keys["alice"], "GET", prefix + "/rooms/" + run["room"] + "/history", expected=403
        )
        checks.append("Revoked room grant also revokes retained receipt/history access")

    result = {
        "state": "passed",
        "checks": checks,
        "count": len(checks),
        "evidenceLevel": "Actual PostgreSQL, HTTP server, authentication, concurrent requests, dispatcher protocol and service restart; execution acknowledgements are fixtures, not model runs",
        "nativeAgentExecutionVerified": False,
        "phoneVerified": False,
    }
    (root / "integration-result.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:18768")
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--unit", default="hollywood-backbone-qualification-20261014.service")
    asyncio.run(main(parser.parse_args()))
