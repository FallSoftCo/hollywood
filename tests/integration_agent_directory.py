"""Real authenticated HTTP/PostgreSQL inventory; no model or worker process."""

import asyncio
import json
import os
import secrets
from pathlib import Path
from uuid import uuid4

from aiohttp import ClientSession, web
from hollywood_backbone.bootstrap import initialize
from hollywood_backbone.server import application
from hollywood_backbone.store import Store, digest


async def main():
    os.umask(0o077)
    root = Path(os.environ["DIRECTORY_EVIDENCE"])
    root.mkdir(mode=0o700)
    db = json.loads(Path(os.environ["DIRECTORY_DATABASE_CONFIG"]).read_text())
    initialize(db, root / "enterprise-a", "Directory qualification A")
    initialize(db, root / "enterprise-b", "Directory qualification B")
    first = json.loads((root / "enterprise-a/installation-intent.json").read_text())
    second = json.loads((root / "enterprise-b/installation-intent.json").read_text())
    people = first["credentials"]
    store = Store(db)
    runner = web.AppRunner(application(store), access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    calls, checks = [], []

    async with ClientSession() as session:
        async def call(token, method, path, payload=None, expected=200):
            async with session.request(
                method, "http://127.0.0.1:" + str(port) + "/hollywood/v3/" + path,
                json=payload, headers={"Authorization": "Bearer " + token},
            ) as response:
                data = await response.json()
                calls.append({"method": method, "path": path, "status": response.status})
                assert response.status == expected, (path, response.status, expected, data)
                return data

        global_token = people["Global"]["token"]
        owner_token = people["Owner"]["token"]
        try:
            created = []
            tokens = []
            for name in ("Unroomed directory engineer", "Unroomed retired engineer"):
                identity, token = str(uuid4()), secrets.token_urlsafe(40)
                await call(owner_token, "POST", "principals", {
                    "operationId": "directory:" + identity,
                    "id": identity, "name": name, "kind": "agent",
                    "enrollmentCredential": token,
                })
                created.append(identity)
                tokens.append(token)
            # Both are intentionally metadata-only and have no membership/worker.
            with store.pool.connection() as c:
                assert c.execute("SELECT count(*) AS n FROM hb_membership WHERE principal=ANY(%s::uuid[])",
                                 (created,)).fetchone()["n"] == 0
                c.execute("UPDATE hb_principal SET active=false WHERE id=%s", (created[1],))
            seen, cursor = [], None
            while True:
                page = await call(global_token, "GET",
                                  "agents?limit=1" + ("&after=" + cursor if cursor else ""))
                assert page["schema"] == "hollywood-agent-directory/v1"
                assert page["authorityId"] == first["authorityId"]
                assert len(page["agents"]) == 1
                for agent in page["agents"]:
                    assert set(agent) == {"id", "authority", "name", "kind", "administrator", "active"}
                    assert agent["authority"] == first["authorityId"] and agent["kind"] == "agent"
                    seen.append(agent["id"])
                    if agent["id"] == created[1]:
                        assert agent["active"] is False
                cursor = page["nextAfter"]
                if cursor is None:
                    break
                assert len(seen) < 10, "Pagination did not terminate"
            assert len(seen) == len(set(seen)) == 3
            assert set(seen) == {people["Global"]["principalId"], *created}
            checks.append("Complete keyset pages include both unroomed identities and inactive enrollment")
            serialized = json.dumps(page)
            assert not any(token in serialized for token in tokens)
            checks.append("Credential values and credential fields are absent")
            await call(tokens[0], "GET", "agents", expected=403)
            await call(tokens[1], "GET", "agents", expected=403)
            checks.append("Nonadministrators and inactive principals cannot enumerate")
            foreign = await call(second["credentials"]["Global"]["token"], "GET", "agents")
            assert {v["id"] for v in foreign["agents"]} == {
                second["credentials"]["Global"]["principalId"]}
            checks.append("Another authority receives only its own identities")
            for query in ("limit=0", "limit=101", "limit=no", "after=invalid", "authorityId=" + second["authorityId"]):
                await call(global_token, "GET", "agents?" + query, expected=400)
            checks.append("Malformed/unbounded/authority-override queries are refused")
            host = str(uuid4())
            await call(owner_token, "POST", "placements", {
                "operationId": "directory:host", "hostId": host,
                "name": "Metadata-only directory fixture",
                "identityFingerprint": digest("metadata-only-r343"),
                "capabilities": {"metadataOnly": True},
                "agentId": people["Global"]["principalId"],
            })
            purpose_tokens = {p: secrets.token_urlsafe(40) for p in ("reader", "writer", "responder")}
            await call(owner_token, "PUT", "agents/" + people["Global"]["principalId"] + "/context-system", {
                "operationId": "directory:purpose-contexts", "hostId": host,
                "workspaceId": str(uuid4()),
                "bindings": [{"purpose": p, "contextId": str(uuid4()),
                              "credentialDigest": digest(token)}
                             for p, token in purpose_tokens.items()],
            })
            native_page = await call(purpose_tokens["writer"], "GET", "agents")
            assert {v["id"] for v in native_page["agents"]} == set(seen)
            await call(purpose_tokens["reader"], "GET", "agents", expected=403)
            checks.append("Actual writer-purpose credential reads directory; reader ceiling is retained")
            with store.pool.connection() as c:
                c.execute("UPDATE hb_principal SET administrator=false WHERE id=%s",
                          (people["Global"]["principalId"],))
            await call(global_token, "GET", "agents", expected=403)
            await call(purpose_tokens["writer"], "GET", "agents", expected=403)
            checks.append("Current administrator revocation applies to primary and purpose credentials")
            result = {"passed": True, "checks": checks, "httpCalls": calls,
                      "observedIdentityCount": len(seen),
                      "evidenceScope": "Actual HTTP/PostgreSQL metadata; no execution/model/production state"}
            (root / "result.json").write_text(json.dumps(result, indent=2) + "\n")
            print(json.dumps(result))
        finally:
            await runner.cleanup()
            store.close()


if __name__ == "__main__":
    asyncio.run(main())
