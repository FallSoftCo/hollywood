"""ONE old-shaped179 -> candidate acceptance journey on isolated real PG/HTTP.

Database and baseline module paths must be supplied by an admitted owner fixture.
No production config/credentials, native, model or cross-host transport is used.
"""
import asyncio
import hashlib
import json
import os
import secrets
import types
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from aiohttp import ClientSession
from aiohttp.test_utils import TestServer
from hollywood_backbone import administrative, server
from hollywood_backbone.federation_domain import kwargs_for
from hollywood_backbone.store import Store


async def main():
    os.umask(0o077)
    root = Path(os.environ["ROOM_UPGRADE_QA"])
    raw = Path(os.environ["ROOM_BASELINE_ADMIN"]).read_bytes()
    expected = "5f26cd5bc724b4265bb528d3fa68ac54174a07858e851aa97803fe1f5b8c597d"
    assert hashlib.sha256(raw).hexdigest() == expected
    baseline = types.ModuleType("hollywood_backbone._baseline179_administrative")
    baseline.__package__ = "hollywood_backbone"
    exec(compile(raw, "retained-baseline179-administrative.py", "exec"), baseline.__dict__)
    store = Store(json.loads((root / "database.json").read_text()))
    store.migrate()
    uid = lambda: str(uuid4())
    authority, agent, room, operation = [uid() for _ in range(4)]
    credentials = {k: {"principalId": uid(), "token": secrets.token_urlsafe(40)}
                   for k in ("Owner", "Global")}
    store.bootstrap(authority, "ISOLATED room upgrade", {"globalRoomId": uid(), "credentials": credentials})
    admin = credentials["Owner"]
    store.principal(admin["principalId"], agent, "ISOLATED retained owner", "agent", secrets.token_urlsafe(40))
    body = {"operationId": operation, "id": room, "name": "ISOLATED original179 room",
            "purpose": "Original acceptance must survive the upgrade", "ownerId": agent}
    old_args = [body[k] for k in ("id", "name", "purpose", "ownerId")]
    command = kwargs_for("create_room", "POST", "rooms", body)["command"]
    original = baseline.mutate(store, admin["principalId"], "create_room", old_args, command)

    def snapshot():
        with store.pool.connection() as c:
            return json.dumps({table: c.execute("SELECT * FROM " + table + " WHERE authority=%s ORDER BY 1,2", (authority,)).fetchall()
                               for table in ("hb_room", "hb_membership", "hb_agent_role", "hb_administrative_command")}, default=str, sort_keys=True)

    before = snapshot()
    api = TestServer(server.application(store), host="127.0.0.1")
    await api.start_server()
    lengths, route_lengths = [], []
    actual_mutate, actual_invoke = administrative.mutate, server.invoke

    def observed_mutate(s, actor, action, arguments, command=None):
        lengths.append(len(arguments))
        return actual_mutate(s, actor, action, arguments, command)

    async def observed_invoke(request, method, *args):
        route_lengths.append(len(args))
        return await actual_invoke(request, method, *args)

    def forbidden_domain_repeat(*_args, **_kwargs):
        raise AssertionError("Original accepted domain mutation was called again")

    report = {"baselineModuleSha256": expected, "seedArgumentCount": len(old_args),
              "realPostgresAndHttp": True, "nativeOrModelCalls": 0}
    try:
        with patch.object(administrative, "mutate", observed_mutate), patch.object(server, "invoke", observed_invoke), patch.object(store, "_admin_create_room", forbidden_domain_repeat):
            same = store.create_room(admin["principalId"], *old_args, command=command)
            same_none = store.create_room(admin["principalId"], *old_args, source_room=None, command=command)
            assert same == same_none == original
            async with ClientSession(headers={"Authorization": "Bearer " + admin["token"]}) as session:
                async with session.post(api.make_url("/hollywood/v3/rooms"), json=body) as response:
                    value = await response.json()
                    assert response.status == 200, (response.status, value)
                    assert value == original
                changed = {**body, "sourceRoomId": uid()}
                async with session.post(api.make_url("/hollywood/v3/rooms"), json=changed) as response:
                    error = await response.json()
                    assert response.status == 409, (response.status, error)
                    assert "contents changed" in error["error"]
        assert snapshot() == before
        # Persist receipt behavior BEFORE shape assertions: the exact baseline
        # compares method/path/wire_digest, not the Python argument list.
        report.update(originalAcceptanceReturned=True, originalRowsUnchanged=True,
                      noSecondDomainMutation=True, changedSourcePayloadRejected=409,
                      storeArgumentCounts=lengths, httpRouteArgumentCounts=route_lengths)
        (root / "receipt-behavior.json").write_text(json.dumps(report, indent=2) + "\n")
        assert lengths == [4, 4, 4, 5], lengths
        assert route_lengths == [4, 5], route_lengths
        report["passed"] = True
        (root / "result.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report))
    finally:
        await api.close()
        store.close()


if __name__ == "__main__":
    asyncio.run(main())
