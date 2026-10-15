"""Actual authenticated PostgreSQL administrative receipts; no model fixture."""
import asyncio, hashlib, json, os, secrets
from pathlib import Path
from uuid import uuid4
from aiohttp import ClientSession, web
from hollywood_backbone.bootstrap import initialize
from hollywood_backbone.server import application
from hollywood_backbone.store import Store, digest

async def main():
    os.umask(0o077)
    root = Path(os.environ["ADMIN_EVIDENCE"]); root.mkdir(mode=0o700)
    db = json.loads(Path(os.environ["ADMIN_DATABASE_CONFIG"]).read_text())
    initialize(db, root / "enterprise", "Administrative acceptance qualification")
    setup = json.loads((root / "enterprise/installation-intent.json").read_text())
    people, room = setup["credentials"], setup["globalRoomId"]
    store = Store(db); runner = None; checks = []
    async def start():
        nonlocal runner
        runner = web.AppRunner(application(store)); await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", 18772).start()
    await start()
    async with ClientSession() as session:
        async def call(who, method, route, payload=None, status=200):
            async with session.request(method, "http://127.0.0.1:18772/hollywood/v3/" + route,
                    json=payload, headers={"Authorization": "Bearer " + people[who]["token"]}) as response:
                result = await response.json()
                assert response.status == status, (route, response.status, status)
                return result
        async def original(who, method, path, body, result):
            receipt = await call(who, "GET", "administration/commands/" + body["operationId"])
            assert receipt["schema"] == "hollywood-administrative-command/v1"
            assert receipt["actorId"] == people[who]["principalId"]
            assert receipt["authorityId"] == setup["authorityId"]
            assert receipt["method"] == method and receipt["path"] == path
            assert receipt["wireDigest"] == digest({"method": method, "path": path, "body": body})
            assert receipt["acceptedResult"] == result
            return receipt
        try:
            agent = str(uuid4()); credential = secrets.token_urlsafe(40)
            principal = {"id": agent, "name": "Receipt engineer", "kind": "agent",
                         "enrollmentCredential": credential, "operationId": "admin:" + str(uuid4())}
            accepted = await asyncio.gather(*[call("Global", "POST", "principals", principal) for _ in range(2)])
            assert accepted[0] == accepted[1] == {"principalId": agent, "created": True}
            receipt = await original("Global", "POST", "principals", principal, accepted[0])
            await call("Global", "POST", "principals", {**principal, "name": "Changed"}, 409)
            with store.pool.connection() as c:
                row = c.execute("SELECT to_jsonb(a)::text AS value FROM hb_administrative_command a WHERE principal=%s AND operation=%s",
                                (people["Global"]["principalId"], principal["operationId"])).fetchone()
                assert credential not in row["value"]
                assert c.execute("SELECT count(*) AS n FROM hb_principal WHERE id=%s", (agent,)).fetchone()["n"] == 1
            assert credential not in json.dumps(receipt)
            checks.append("Concurrent identical principal operation retains one original acceptance; changed content conflicts; no enrollment secret retained")
            await call("Owner", "GET", "administration/commands/" + principal["operationId"], status=403)
            people["Worker"] = {"principalId": agent, "token": credential}
            await call("Worker", "GET", "administration/commands/" + principal["operationId"], status=403)
            checks.append("Other administrator and non-administrator cannot read issuer-private receipt")
            newroom = str(uuid4())
            body = {"id": newroom, "name": "Receipt engineering", "purpose": "Actual metadata qualification",
                    "ownerId": agent, "operationId": "admin:" + str(uuid4())}
            created = await call("Global", "POST", "rooms", body)
            await original("Global", "POST", "rooms", body, created)
            rolepath = "agents/" + agent + "/role"
            role = await call("Global", "GET", rolepath)
            body = {"primary": "engineer", "purpose": "Original accepted role", "homeRoomId": newroom,
                    "revision": role["revision"], "operationId": "admin:" + str(uuid4())}
            role1 = await call("Global", "PUT", rolepath, body)
            role2 = await call("Global", "PUT", rolepath,
                {**body, "operationId": "admin:" + str(uuid4()), "revision": role1["revision"], "purpose": "Later role"})
            assert role2["revision"] > role1["revision"]
            await original("Global", "PUT", rolepath, body, role1)
            assert await call("Global", "PUT", rolepath, body) == role1
            checks.append("Room and role acceptance survive later mutable role revision without reconstruction")
            membership = "rooms/" + room + "/membership"
            body = {"principalId": agent, "can_read": True, "can_post": True, "can_request": True,
                    "revision": 0, "operationId": "admin:" + str(uuid4())}
            granted = await call("Global", "PUT", membership, body)
            await call("Global", "PUT", membership,
                {**body, "can_read": False, "can_post": False, "can_request": False,
                 "revision": granted["revision"], "operationId": "admin:" + str(uuid4())})
            await original("Global", "PUT", membership, body, granted)
            checks.append("Membership original grant remains distinct from current revoked grant")
            body = {"hostId": str(uuid4()), "name": "Private administrative test",
                    "identityFingerprint": hashlib.sha256(b"qualification").hexdigest(),
                    "capabilities": {"metadataOnly": True}, "agentId": agent,
                    "operationId": "admin:" + str(uuid4())}
            placement = await call("Global", "POST", "placements", body)
            await original("Global", "POST", "placements", body, placement)
            checks.append("Host placement retains original acceptance in its mutation transaction")
            bad = {"primary": "global", "purpose": "Forbidden escalation", "homeRoomId": newroom,
                   "revision": role2["revision"], "operationId": "admin:" + str(uuid4())}
            await call("Global", "PUT", rolepath, bad, 403)
            with store.pool.connection() as c:
                assert c.execute("SELECT count(*) AS n FROM hb_administrative_command WHERE operation=%s",
                                 (bad["operationId"],)).fetchone()["n"] == 0
            checks.append("Rejected privilege escalation leaves no accepted command receipt")
            await runner.cleanup(); await start()
            assert await call("Global", "GET", "administration/commands/" + principal["operationId"]) == receipt
            checks.append("API restart retains original command")
            with store.pool.connection() as c:
                c.execute("UPDATE hb_principal SET administrator=false WHERE id=%s", (people["Global"]["principalId"],))
            await call("Global", "GET", "administration/commands/" + principal["operationId"], status=403)
            checks.append("Current administrative revocation prevents original receipt reads")
            result = {"passed": True, "checks": checks, "nativeModelsStarted": 0, "productionChanged": False}
            (root / "result.json").write_text(json.dumps(result, indent=2) + "\n")
            print(json.dumps(result))
        finally:
            await runner.cleanup(); store.close()

if __name__ == "__main__":
    asyncio.run(main())
