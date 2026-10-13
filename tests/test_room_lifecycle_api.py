"""Independent HTTP and persistence checks; every Codex RPC is a local fake."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from aiohttp import ClientSession, web

from hollywood_room.runtime import Runtime
from hollywood_room.server import Service, application
from hollywood_room.store import Conflict, Store


@pytest.fixture
async def lifecycle(tmp_path):
    (tmp_path / "auth.json").write_text(json.dumps({"tokens": {"account_id": "owner"}}))
    store = Store(tmp_path / "room.db")
    with store.db:
        store.ensure_binding("lobby", "maya")
        store.db.execute("UPDATE binding SET thread='saved-thread',cursor=17 WHERE agent='maya'")
        store.event("message", "lobby", "maya", "Preserved history", visibility="direct:maya")
    runtime = Runtime(store, binary="unused", home=tmp_path, state=tmp_path, workspace=tmp_path)
    runtime.limits.clock = lambda: 1000
    state = {"accountId": "owner", "used": 99, "credits": 2, "secondary": None}
    consumed = []

    async def rpc(method, params):
        await asyncio.sleep(0)
        if method == "account/read":
            return {"account": {"type": "chatgpt", "email": "owner@example.test"}}
        if method == "account/rateLimits/read":
            return {
                "accountId": state["accountId"],
                "rateLimits": {
                    "primary": {"usedPercent": state["used"], "resetsAt": state.get("reset", 2000)},
                    "secondary": state["secondary"],
                },
                "rateLimitResetCredits": {"availableCount": state["credits"]},
            }
        assert method == "account/rateLimitResetCredit/consume"
        consumed.append(params)
        state["credits"] -= 1
        return {"outcome": "reset"}

    runtime.rpc.call = rpc
    runner = web.AppRunner(application(Service(store, "review-token", runtime)))
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    base = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/hollywood/v2"
    async with ClientSession(headers={"Authorization": "Bearer review-token"}) as client:
        yield SimpleNamespace(
            store=store,
            runtime=runtime,
            client=client,
            base=base,
            consumed=consumed,
            state=state,
            root=tmp_path,
        )
    await runner.cleanup()
    store.close()


async def test_auth_and_unknown_team_cannot_redeem(lifecycle):
    h = lifecycle
    snapshot = await h.runtime.limits.read()
    payload = {"commandId": "scope", "expectedAccountKey": snapshot["accountKey"]}
    response = await h.client.post(
        h.base + "/limits/reset", json=payload, headers={"Authorization": "Bearer wrong"}
    )
    assert response.status == 401
    response = await h.client.post(h.base + "/teams/missing/limits/reset", json=payload)
    assert response.status == 400
    assert h.consumed == []


async def test_concurrent_new_and_saved_resume_preserve_history(lifecycle):
    h = lifecycle
    original = h.runtime.conversations.list("lobby", "maya", "default")["currentConversationId"]
    history = h.store.events(visibility="direct:maya")
    payload = {"commandId": "new", "task": "lobby", "agent": "maya"}
    responses = await asyncio.gather(
        *(h.client.post(h.base + "/conversations", json=payload) for _ in range(2))
    )
    assert [r.status for r in responses] == [200, 200]
    first, second = await asyncio.gather(*(r.json() for r in responses))
    assert first == second
    listing = h.runtime.conversations.list("lobby", "maya", "default")
    assert len(listing["data"]) == 2
    assert listing["currentConversationId"] == first["conversationId"]
    response = await h.client.post(
        h.base + f"/conversations/{original}/control",
        json={"commandId": "resume", "action": "resume"},
    )
    assert response.status == 200
    assert h.store.rows("SELECT thread,cursor FROM binding WHERE agent='maya'") == [
        {"thread": "saved-thread", "cursor": 17}
    ]
    assert h.store.events(visibility="direct:maya") == history
    response = await h.client.get(h.base + "/teams/missing/conversations?task=lobby&agent=maya")
    assert response.status == 400


async def test_reset_concurrent_retry_and_restart_keep_thread_and_receipt(lifecycle):
    h = lifecycle
    before = h.store.rows("SELECT * FROM binding")
    history = h.store.events(visibility="direct:maya")
    snapshot = await h.runtime.limits.read()
    payload = {"commandId": "reset", "expectedAccountKey": snapshot["accountKey"]}
    wrong = await h.client.post(
        h.base + "/limits/reset", json={**payload, "expectedAccountKey": "x" * 64}
    )
    assert wrong.status == 409
    responses = await asyncio.gather(
        *(h.client.post(h.base + "/limits/reset", json=payload) for _ in range(2))
    )
    assert [r.status for r in responses] == [200, 200]
    results = await asyncio.gather(*(r.json() for r in responses))
    assert all(r["limits"]["remainingPercent"] == 1 for r in results)
    assert h.consumed == [{"idempotencyKey": "reset"}]
    h.store.close()
    reopened = Store(h.root / "room.db")
    try:
        restarted = Runtime(reopened, binary="unused", home=h.root, state=h.root, workspace=h.root)
        restarted.rpc.call = h.runtime.rpc.call
        restarted.limits.clock = lambda: 1000
        replay = await restarted.limits.reset("reset", expected_account_key=snapshot["accountKey"])
        assert replay["outcome"] == "reset"
        assert h.consumed == [{"idempotencyKey": "reset"}]
        assert reopened.rows("SELECT * FROM binding") == before
        assert reopened.events(visibility="direct:maya") == history
    finally:
        reopened.close()


@pytest.mark.parametrize("case", ["account", "expired", "nan"])
async def test_automatic_reset_rejects_invalid_observation(lifecycle, case):
    h = lifecycle
    if case == "account":
        h.state["accountId"] = "another-owner"
    elif case == "expired":
        h.state["reset"] = 900
        h.state["secondary"] = {"usedPercent": 0, "resetsAt": 2000}
    else:
        h.state["used"] = float("nan")
    try:
        await h.runtime.limits.automatic()
    except (ValueError, Conflict):
        pass
    assert h.consumed == []
