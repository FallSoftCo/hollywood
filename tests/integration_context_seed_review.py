"""Authenticated human canonical seed preview on real PostgreSQL and loopback HTTP.

This uses a disposable schema and declared source/native-binding fixtures.
No model, worker, production message or native window is started.
"""
import argparse
import asyncio
import hashlib
import json
import os
import secrets
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from aiohttp import ClientSession, web
from hollywood_backbone.server import application
from hollywood_backbone.store import Store, digest
from hollywood_backbone.context_material import wire


def uid():
    return str(uuid4())


async def run(args):
    os.umask(0o077)
    evidence = args.evidence
    evidence.mkdir(mode=0o700, parents=True, exist_ok=False)
    config = json.loads(args.database_config.read_text())
    schema = "seed_review_" + uuid4().hex
    (evidence / "intent.json").write_text(json.dumps({
        "schema": schema, "production": False,
        "mode": "Actual HTTP/PostgreSQL; declared fixture sources and bindings; no model",
        "ownedSchemaRetainedForReceipts": True,
    }, indent=2))
    with psycopg.connect(**config) as c:
        c.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    scoped = {**config, "options": "-csearch_path=" + schema}
    (evidence / "database-private.json").write_text(json.dumps(scoped))
    store = Store(scoped)
    receipts, checks = [], []
    runner = None
    try:
        store.migrate()
        plans = []
        for n in range(2):
            plan = {"globalRoomId": uid(), "credentials": {
                key: {"principalId": uid(), "token": secrets.token_urlsafe(40)}
                for key in ("Owner", "Global")}}
            store.bootstrap(uid(), "Seed qualification " + str(n), plan)
            plans.append(plan)
        (evidence / "credentials-private.json").write_text(json.dumps(plans))
        owner = plans[0]["credentials"]["Owner"]["token"]
        foreign = plans[1]["credentials"]["Owner"]["token"]
        agent_admin = plans[0]["credentials"]["Global"]["token"]
        room = plans[0]["globalRoomId"]
        peer, host, draft = uid(), uid(), uid()
        peer_token = secrets.token_urlsafe(40)
        purpose_tokens = {name: secrets.token_urlsafe(40)
                          for name in ("reader", "writer", "responder")}
        runner = web.AppRunner(application(store), access_log=None)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        prefix = "/hollywood/v3"
        base = prefix + "/agents/" + peer + "/contexts/reader"

        async with ClientSession() as session:
            async def call(token, method, path, data=None, status=200):
                async with session.request(method, "http://127.0.0.1:" + str(port) + path,
                        headers={"Authorization": "Bearer " + token}, json=data) as response:
                    result = await response.json()
                    receipts.append({"method": method, "path": path,
                                     "status": response.status, "result": result})
                    (evidence / "http-private.json").write_text(json.dumps(receipts, indent=2))
                    assert response.status == status, (method, path, response.status, result)
                    return result

            await call(owner, "POST", prefix + "/principals", {
                "id": peer, "name": "Unstarted seed QA agent", "kind": "agent",
                "enrollmentCredential": peer_token})
            await call(owner, "PUT", prefix + "/rooms/" + room + "/membership", {
                "principalId": peer, "can_read": True, "can_post": False,
                "can_request": False, "revision": 0})
            await call(owner, "POST", prefix + "/placements", {
                "hostId": host, "name": "Metadata-only QA host",
                "identityFingerprint": "declared-fixture-not-an-installed-native-host",
                "capabilities": {}, "agentId": peer})
            await call(owner, "PUT", prefix + "/agents/" + peer + "/context-system", {
                "hostId": host, "workspaceId": "metadata-only-no-process",
                "bindings": [{"purpose": name, "contextId": uid(),
                              "credentialDigest": digest(token)}
                             for name, token in purpose_tokens.items()]})

            async def message(operation, body, **extra):
                return await call(owner, "POST", prefix + "/rooms/" + room + "/messages", {
                    "operationId": operation, "kind": "message", "body": body,
                    "audience": "room", **extra})

            learned = await message("fixture-learned-fact",
                "Declared closing-window fixture: invoice export requires the nautical locale.")
            correction = await message("fixture-user-correction",
                "Declared user correction: preserve the existing ledger IDs.")
            reference = "event:" + learned["event"]["id"]
            correction_ref = "event:" + correction["event"]["id"]
            # Existing model selection is separate metadata. It must survive human edits.
            with store.pool.connection() as c:
                c.execute("""INSERT INTO hb_context_selection
                    (agent,purpose,reference,reason,selected_revision)
                    VALUES(%s,'reader',%s,'Existing model relevance fixture',1)""",
                    (peer, correction_ref))
                before = c.execute("SELECT * FROM hb_purpose_context WHERE agent=%s ORDER BY purpose",
                                   (peer,)).fetchall()

            create = {"id": draft, "operationId": "create-original-draft"}
            created = await call(owner, "POST", base + "/seed-drafts", create)
            revision = created["draft"]["revision"]
            path = base + "/seed-drafts/" + draft
            original_receipt = await call(owner, "GET", path + "/operations/create-original-draft")
            assert original_receipt == created
            assert await call(owner, "POST", base + "/seed-drafts", create) == created
            checks.append("Original human draft creation reconciles by receipt without a model or new window")

            async def mark(ref, operation, keep=True, source=None, rev=None):
                nonlocal revision
                source = source or (await call(owner, "GET",
                    base + "/seed-sources?reference=" + ref))["record"]
                payload = {"operationId": operation, "revision": revision if rev is None else rev,
                           "reference": ref, "sourceRevision": source["revision"],
                           "sourceSha256": source["sha256"], "keep": keep,
                           "reason": "Explicit human retention"}
                result = await call(owner, "PUT", path + "/marks", payload)
                revision = result["draft"]["revision"]
                return payload, result

            source = (await call(owner, "GET", base + "/seed-sources?reference=" + reference))["record"]
            assert source["content"]["event"]["body"] == learned["event"]["body"]
            assert hashlib.sha256(wire(source["content"]).encode()).hexdigest() == source["sha256"]
            first_payload, first_mark = await mark(reference, "pin-learned", source=source)
            await mark(correction_ref, "pin-correction")
            preview = await call(owner, "GET", path)
            content = json.loads("".join(p["content"] for p in preview["snapshot"]["chunks"]))
            assert {x["reference"] for x in content["records"]} == {"role", reference, correction_ref}
            assert learned["event"]["body"] in wire(content)
            assert correction["event"]["body"] in wire(content)
            assert preview["size"]["canonicalBytes"] == len(wire(content).encode())
            assert digest(content) == preview["canonicalContentSha256"]
            assert not preview["nativeInputReady"] and not preview["nativeWindowChanged"]
            assert preview["size"]["estimatedTokens"] is None
            assert all(x["authorKind"] == "human" for x in preview["draft"]["marks"].values())
            checks.append("Human fact/correction pins have exact source hashes, canonical bytes and provenance")

            await call(owner, "PUT", path + "/marks",
                       {**first_payload, "operationId": "stale-revision"}, status=409)
            await call(owner, "PUT", path + "/marks",
                       {**first_payload, "reason": "Changed original payload"}, status=409)
            await call(owner, "PUT", path + "/marks",
                       {**first_payload, "operationId": "bool-revision", "revision": True}, status=400)
            assert await call(owner, "GET", path + "/operations/pin-learned") == first_mark
            await mark(reference, "unpin-learned", keep=False, source=source)
            after = await call(owner, "GET", path)
            assert reference not in after["draft"]["marks"]
            assert (await call(owner, "GET", base + "/seed-sources?reference=" + reference))["record"] == source
            checks.append("CAS and immutable operation receipts prevent changed writes; unpin preserves original source")

            role = (await call(owner, "GET", base + "/seed-sources?reference=role"))["record"]
            await call(owner, "PUT", path + "/marks", {
                "operationId": "cannot-remove-role", "revision": revision, "reference": "role",
                "sourceRevision": role["revision"], "sourceSha256": role["sha256"],
                "keep": False, "reason": "Must refuse"}, status=409)
            await call(owner, "PUT", prefix + "/agents/" + peer + "/role", {
                "primary": "worker", "purpose": "Revised canonical responsibility",
                "homeRoomId": room, "revision": role["revision"]})
            changed = await call(owner, "GET", path, status=409)
            assert changed["details"]["reason"] == "source_changed"
            await mark("role", "review-current-role")
            assert (await call(owner, "GET", path))["nativeInputReady"] is False
            checks.append("Role is mandatory; changed canonical role requires explicit review and repin")

            await call(foreign, "GET", path, status=403)
            await call(agent_admin, "GET", path, status=403)
            await call(purpose_tokens["writer"], "GET", path, status=403)
            await call(purpose_tokens["reader"], "POST", base + "/seed-drafts",
                       {"id": uid(), "operationId": "not-human"}, status=403)
            await call(owner, "GET", base.replace("/reader", "/writer") +
                       "/seed-sources?reference=" + reference, status=403)
            private = await message("fixture-private-human", "Unavailable private source",
                                    audience="direct",
                                    recipientId=plans[0]["credentials"]["Owner"]["principalId"])
            await call(owner, "GET", base + "/seed-sources?reference=event:" +
                       private["event"]["id"], status=403)
            checks.append("Foreign human, agent-admin, purpose token and wrong-purpose/private sources refused")

            # Membership revocation does not make a durable pin an access grant.
            await call(owner, "PUT", prefix + "/rooms/" + room + "/membership", {
                "principalId": peer, "can_read": False, "can_post": False,
                "can_request": False, "revision": 1})
            await call(owner, "GET", path, status=403)
            await call(owner, "PUT", prefix + "/rooms/" + room + "/membership", {
                "principalId": peer, "can_read": True, "can_post": False,
                "can_request": False, "revision": 2})
            assert (await call(owner, "GET", path))["nativeWindowChanged"] is False
            checks.append("Preview reauthorizes source membership; pins restore no revoked access")

            for n in range(9):
                event = await message("oversize-fixture-" + str(n), "漢" * 2000)
                await mark("event:" + event["event"]["id"], "oversize-pin-" + str(n))
            over = await call(owner, "GET", path, status=409)
            assert over["details"]["reason"] == "canonical_budget_exceeded"
            assert over["details"]["totalBytes"] > over["details"]["limitBytes"]
            last = await call(owner, "GET", path + "/operations/oversize-pin-8")
            assert len(last["draft"]["marks"]) == 11
            checks.append("Oversized canonical preview refuses before closure without truncating human pins")

            with store.pool.connection() as c:
                now = c.execute("SELECT * FROM hb_purpose_context WHERE agent=%s ORDER BY purpose",
                                (peer,)).fetchall()
                # The canonical role changed intentionally; native purpose state did not.
                assert now == before
                selections = c.execute("SELECT reference,reason FROM hb_context_selection WHERE agent=%s",
                                       (peer,)).fetchall()
                assert selections == [{"reference": correction_ref, "reason": "Existing model relevance fixture"}]
                counts = {table: c.execute(sql.SQL("SELECT count(*) AS n FROM {}").format(
                    sql.Identifier(table))).fetchone()["n"]
                    for table in ("hb_context_permit", "hb_delivery", "hb_context_operation")}
                assert counts == {key: 0 for key in counts}
            checks.append("All native bindings, runtime marks, work deliveries and inference permits unchanged")
        result = {"state": "canonical_preview_integration_passed", "checks": checks,
                  "schema": schema, "actualHttp": True, "actualPostgres": True,
                  "semanticModelProof": False, "modelsStarted": 0, "productionChanged": False,
                  "nativeRefreshQualified": False, "physicalPhoneProof": False,
                  "requestCount": len(receipts)}
        (evidence / "result.json").write_text(json.dumps(result, indent=2))
        print(json.dumps(result))
    finally:
        if runner is not None:
            await runner.cleanup()
        store.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-config", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    asyncio.run(run(parser.parse_args()))
