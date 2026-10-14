"""Actual PostgreSQL/HTTP room activity, followed by one finite native LAX turn.

Retain every intent/receipt privately. An existing intent is never replayed.
No historical session, product worker, phone profile or production service is used.
"""

import argparse
import asyncio
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from aiohttp import ClientSession
from lax_connector.connector import saved

from hollywood_backbone.bootstrap import initialize


def uid():
    return str(uuid.uuid4())


async def main(args):
    os.umask(0o077)
    root = args.state
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    intent = root / "scenario-intent.json"
    if intent.exists():
        raise RuntimeError(
            "Original scenario intent exists; inspect its exact receipts without resubmission"
        )
    ids = {name: uid() for name in ("worker", "native", "bob", "room", "host", "attention")}
    keys = {name: secrets.token_urlsafe(40) for name in ("worker", "native", "bob")}
    saved(intent, {"ids": ids, "keys": keys, "unit": args.native_unit})
    initialize(
        json.loads(args.database.read_text()),
        root / "installation",
        "Room execution and human control qualification",
    )
    install = json.loads((root / "installation/installation-intent.json").read_text())
    owner = install["credentials"]["Owner"]["token"]
    checks, receipts = [], []
    async with ClientSession() as session:

        async def call(key, method, path, data=None, expected=200):
            async with session.request(
                method,
                args.url + "/hollywood/v3/" + path,
                json=data,
                headers={"Authorization": "Bearer " + key},
            ) as response:
                result = await response.json()
                receipts.append(
                    {"method": method, "path": path, "status": response.status, "result": result}
                )
                saved(root / "private-receipts.json", receipts)
                assert response.status == expected, (method, path, response.status, expected)
                return result

        async def restart():
            subprocess.run(["systemctl", "--user", "restart", args.api_unit], check=True)
            for _ in range(40):
                try:
                    async with session.get(args.url + "/health") as response:
                        if response.status == 200:
                            return
                except OSError:
                    pass
                await asyncio.sleep(0.25)
            raise RuntimeError("Owned candidate API failed to recover")

        for name in ("worker", "native", "bob"):
            await call(
                owner,
                "POST",
                "principals",
                {
                    "id": ids[name],
                    "name": name,
                    "kind": "human" if name == "bob" else "agent",
                    "enrollmentCredential": keys[name],
                },
            )
        await call(
            owner,
            "POST",
            "rooms",
            {
                "id": ids["room"],
                "name": "Qualification",
                "purpose": "Disposable room transcript and human controls",
                "ownerId": ids["worker"],
            },
        )
        for name in ("native", "bob"):
            await call(
                owner,
                "PUT",
                "rooms/" + ids["room"] + "/membership",
                {
                    "principalId": ids[name],
                    "can_read": True,
                    "can_post": name == "native",
                    "can_request": False,
                    "revision": 0,
                },
            )
        for name in ("worker", "native"):
            await call(
                owner,
                "POST",
                "placements",
                {
                    "hostId": ids["host"],
                    "name": "OZZZ qualification",
                    "identityFingerprint": hashlib.sha256(
                        Path("/etc/machine-id").read_bytes()
                    ).hexdigest(),
                    "capabilities": {"qualification": True},
                    "agentId": ids[name],
                },
            )

        async def request(operation, body, agent="worker", audience="room"):
            return await call(
                owner,
                "POST",
                "rooms/" + ids["room"] + "/messages",
                {
                    "operationId": operation,
                    "kind": "request",
                    "body": body,
                    "audience": audience,
                    "recipientId": ids[agent],
                },
            )

        await request("protocol-fixture", "Protocol fixture only; no native execution is asserted")
        claim = await call(
            keys["worker"],
            "POST",
            "connector/claim",
            {"operationId": "protocol-fixture-claim", "hostId": ids["host"]},
        )
        d = claim["delivery"]
        payload = {
            "claimId": d["claim_id"],
            "generation": d["generation"],
            "nativeThread": "fixture-thread",
            "nativeTurn": "fixture-turn",
            "itemId": "command-one",
            "phase": "started",
            "nativeItemDigest": "a" * 64,
            "item": {
                "nativeType": "commandExecution",
                "title": "Command",
                "status": "inProgress",
                "command": "printf qualification",
                "cwd": "/qualification",
            },
        }
        path = "connector/deliveries/" + d["id"] + "/activity"
        started = await call(keys["worker"], "POST", path, payload)
        assert started["deliveries"] == [] and started["event"]["source"] == claim["request"]["id"]
        duplicate = await call(keys["worker"], "POST", path, payload)
        assert duplicate == started
        await call(
            keys["worker"],
            "POST",
            path,
            {**payload, "item": {**payload["item"], "command": "changed"}},
            409,
        )
        await call(
            keys["worker"], "POST", path, {**payload, "generation": d["generation"] + 1}, 409
        )
        await call(keys["native"], "POST", path, payload, 403)
        await call(keys["worker"], "POST", path, {**payload, "nativeTurn": "another-turn"}, 409)
        checks.append(
            "Activity is source/agent/host/generation/turn bound; same identity dedups and changed contents conflict; no delivery fanout"
        )
        complete = {
            **payload,
            "phase": "completed",
            "nativeItemDigest": "b" * 64,
            "item": {
                **payload["item"],
                "status": "failed",
                "exitCode": 1,
                "durationMs": 10,
                "output": "fixture failure",
            },
        }
        done = await call(keys["worker"], "POST", path, complete)
        assert done["event"]["id"] != started["event"]["id"]
        await restart()
        assert await call(keys["worker"], "POST", path, complete) == done
        history = await call(owner, "GET", "rooms/" + ids["room"] + "/history")
        activity = [e for e in history["events"] if e["data"].get("type") == "executionActivity"]
        assert len(activity) == 2 and [e["data"]["phase"] for e in activity] == [
            "started",
            "completed",
        ]
        checks.append(
            "Both immutable phases survive API restart; reconnect replay has one accepted record for each phase"
        )

        attention = {
            "id": ids["attention"],
            "deliveryId": d["id"],
            "claimId": d["claim_id"],
            "generation": d["generation"],
            "nativeThread": "fixture-thread",
            "nativeTurn": "fixture-turn",
            "nativeSlot": "fixture-question",
            "method": "hollywood/question",
            "summary": "Choose a value",
            "request": {
                "questions": [{"id": "value", "header": "Value", "question": "Choose a value"}]
            },
        }
        question = await call(keys["worker"], "POST", "attention", attention)
        apath = "attention/" + ids["attention"]
        await call(keys["bob"], "GET", apath, expected=403)
        reply = {
            "operationId": "fixture-answer",
            "scope": question["scope"],
            "response": {"answers": {"value": {"answers": ["SELECTED"]}}},
        }
        await call(
            owner,
            "POST",
            apath + "/response",
            {**reply, "scope": {**reply["scope"], "generation": d["generation"] + 1}},
            409,
        )
        answered = await call(owner, "POST", apath + "/response", reply)
        assert await call(owner, "POST", apath + "/response", reply) == answered
        await call(
            owner,
            "POST",
            apath + "/response",
            {**reply, "response": {"answers": {"value": {"answers": ["CHANGED"]}}}},
            409,
        )
        ack = {
            "operationId": "fixture-native-write",
            "scope": question["scope"],
            "claimId": d["claim_id"],
            "generation": d["generation"],
            "state": "native_submitted",
        }
        await call(keys["worker"], "POST", apath + "/acknowledge", ack)
        await call(
            keys["worker"],
            "POST",
            "deliveries/" + d["id"] + "/transition",
            {
                "operationId": "fixture-terminal",
                "claimId": d["claim_id"],
                "generation": d["generation"],
                "state": "failed",
                "nativeThread": "fixture-thread",
                "nativeTurn": "fixture-turn",
                "body": "Fixture terminal receipt; no model execution is claimed",
            },
        )
        assert (await call(owner, "GET", apath))["attention"]["state"] == "closed"
        assert (await call(keys["worker"], "POST", path, complete)) == done
        checks.append(
            "Human control is private, exact-scope, idempotent and closed with its original terminal execution; native-write acknowledgement is distinct"
        )

        await request("private-fixture", "Private protocol fixture", audience="direct")
        private = await call(
            keys["worker"],
            "POST",
            "connector/claim",
            {"operationId": "private-fixture-claim", "hostId": ids["host"]},
        )
        pd = private["delivery"]
        private_payload = {**payload, "claimId": pd["claim_id"], "generation": pd["generation"]}
        receipt = await call(
            keys["worker"],
            "POST",
            "connector/deliveries/" + pd["id"] + "/activity",
            private_payload,
        )
        assert (
            receipt["event"]["audience"] == "direct"
            and receipt["event"]["recipient"] == install["credentials"]["Owner"]["principalId"]
        )
        bob = await call(keys["bob"], "GET", "rooms/" + ids["room"] + "/history")
        assert receipt["event"]["id"] not in [e["id"] for e in bob["events"]]
        await call(
            keys["worker"],
            "POST",
            "deliveries/" + pd["id"] + "/transition",
            {
                "operationId": "private-fixture-stop",
                "claimId": pd["claim_id"],
                "generation": pd["generation"],
                "state": "failed",
                "body": "Fixture ended without native execution",
            },
        )
        checks.append(
            "Private execution activity inherits its original direct audience; room membership does not disclose it"
        )

        work, home, state = (root / name for name in ("work", "codex-home", "connector-state"))
        for directory in (work, home, state):
            directory.mkdir(mode=0o700)
        shutil.copy2(args.auth, home / "auth.json")
        os.chmod(home / "auth.json", 0o600)
        config = {
            "hollywoodUrl": args.url,
            "token": keys["native"],
            "agentId": ids["native"],
            "hostId": ids["host"],
            "state": str(state),
            "workspace": str(work),
            "codexHome": str(home),
            "binary": str(args.binary),
            "model": "gpt-6.1-sol",
            "effort": "xhigh",
            "sandbox": "workspace-write",
            "instructions": "Execute only this finite disposable qualification; do not poll models or wake historical agents.",
            "turnDeadlineSeconds": 600,
        }
        saved(root / "connector.json", config)
        native_request = await request(
            "native-question-command",
            "This is a finite integration qualification. First use hollywood_ask exactly once to ask one question with id value, header Value, and text 'What value should the proof contain?'. Do not guess the answer. After receiving the answer, use a shell command to create answer.txt containing only that answer plus newline in the current workspace and print the file. Send one brief intermediate message saying the answer was received, then finish with a brief report. Do not delegate, schedule or start any other work.",
            agent="native",
        )
        saved(root / "native-admission.json", {"request": native_request, "unit": args.native_unit})
        env = os.environ["PYTHONPATH"]
        subprocess.run(
            [
                "systemd-run",
                "--user",
                "--unit=" + args.native_unit,
                "--property=WorkingDirectory=" + str(root),
                "--property=UMask=0077",
                "--property=MemoryMax=2G",
                "--property=CPUQuota=200%",
                "--property=RuntimeMaxSec=700",
                "--setenv=PYTHONPATH=" + env,
                str(args.python),
                "-m",
                "lax_connector.connector",
                "--config",
                str(root / "connector.json"),
                "--claim-id",
                "native-question-command-claim",
            ],
            check=True,
        )
        deadline, answered_native = time.monotonic() + 650, None
        while time.monotonic() < deadline:
            page = await call(owner, "GET", "rooms/" + ids["room"] + "/attention")
            candidates = [q for q in page["attention"] if q["attention"]["agent"] == ids["native"]]
            if candidates and answered_native is None:
                assert len(candidates) == 1
                q = candidates[0]
                assert q["attention"]["state"] == "pending"
                saved(root / "native-question-original.json", q)
                await restart()
                restored = await call(owner, "GET", "attention/" + q["attention"]["id"])
                assert (
                    restored["scope"] == q["scope"] and restored["attention"]["state"] == "pending"
                )
                native_reply = {
                    "operationId": "native-human-answer",
                    "scope": q["scope"],
                    "response": {"answers": {"value": {"answers": ["HOLLYWOOD_ROOM_VERIFIED"]}}},
                }
                saved(root / "native-answer-intent.json", native_reply)
                answered_native = await call(
                    owner, "POST", "attention/" + q["attention"]["id"] + "/response", native_reply
                )
                saved(root / "native-answer-receipt.json", answered_native)
            current = await call(owner, "GET", "commands/native-question-command")
            delivery = current["deliveries"][0]
            if delivery["state"] in ("completed", "failed", "cancelled", "unknown"):
                saved(root / "native-final-receipt.json", current)
                assert delivery["state"] == "completed", delivery["state"]
                break
            await asyncio.sleep(1)
        else:
            raise RuntimeError(
                "Original native scenario remains unresolved; inspect exact unit/journals, never replay"
            )
        assert answered_native is not None
        artifact = work / "answer.txt"
        assert artifact.read_text() == "HOLLYWOOD_ROOM_VERIFIED\n"
        full = await call(owner, "GET", "rooms/" + ids["room"] + "/history?limit=100")
        events = [
            e
            for e in full["events"]
            if e["source"] == native_request["event"]["id"]
            or e["id"] == native_request["event"]["id"]
        ]
        saved(root / "native-room-history.json", {"events": events})
        operations = [e for e in events if e["data"].get("nativeType") == "commandExecution"]
        assert any(e["data"]["phase"] == "started" for e in operations)
        assert any(
            e["data"]["phase"] == "completed"
            and e["data"]["exitCode"] == 0
            and "HOLLYWOOD_ROOM_VERIFIED" in (e["data"].get("output") or "")
            for e in operations
        )
        assert len({(e["data"]["itemId"], e["data"]["phase"]) for e in operations}) == len(
            operations
        )
        assert all(
            e["author"] == ids["native"]
            and e["data"]["hostId"] == ids["host"]
            and e["data"]["turn"] == delivery["native_turn"]
            for e in operations
        )
        latest = await call(owner, "GET", "attention/" + answered_native["attention"]["id"])
        assert latest["attention"]["state"] == "closed"
        checks.append(
            "One real xhigh native LAX turn waits for an attributed Hollywood human answer across API restart, resumes the same turn, writes the exact answer and publishes started/completed command records with host/agent correlation"
        )
        result = {
            "state": "passed",
            "checks": checks,
            "authorityId": install["authorityId"],
            "nativeThread": delivery["native_thread"],
            "nativeTurn": delivery["native_turn"],
            "commandItems": len({e["data"]["itemId"] for e in operations}),
            "artifactSha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
            "physicalPhoneVerified": False,
            "productionChanged": False,
        }
        saved(root / "result.json", result)
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--auth", required=True, type=Path)
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--python", required=True, type=Path)
    parser.add_argument("--url", default="http://127.0.0.1:18768")
    parser.add_argument("--api-unit", default="hollywood-backbone-qualification-20261014.service")
    parser.add_argument("--native-unit", default="lax-room-activity-native-qualification-20261014")
    asyncio.run(main(parser.parse_args()))
