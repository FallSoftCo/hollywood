import asyncio

import pytest

from hollywood_room.approvals import Approvals
from hollywood_room.store import Conflict, Store


async def test_approval_response_only_reaches_its_live_request(tmp_path):
    store = Store(tmp_path / "room.db")
    approvals = Approvals(store)
    pending = asyncio.create_task(
        approvals.request(
            "item/commandExecution/requestApproval",
            {"threadId": "thread", "turnId": "turn", "command": "review-me"},
            task="lobby",
            agent="maya",
            visibility="room",
        )
    )
    await asyncio.sleep(0)
    event = store.snapshot()["attention"][0]["id"]
    approvals.answer(event, "accept")
    assert await pending == {"decision": "accept"}
    with pytest.raises(Conflict):
        approvals.answer(event, "accept")
    store.close()


async def test_runtime_loss_expires_durable_approval(tmp_path):
    store = Store(tmp_path / "room.db")
    approvals = Approvals(store)
    pending = asyncio.create_task(
        approvals.request(
            "item/fileChange/requestApproval",
            {"threadId": "thread", "turnId": "turn"},
            task="lobby",
            agent="theo",
            visibility="room",
        )
    )
    await asyncio.sleep(0)
    event = store.snapshot()["attention"][0]["id"]
    approvals.expire()
    assert await pending == {"decision": "decline"}
    with pytest.raises(Conflict):
        approvals.answer(event, "accept")
    assert store.snapshot()["attention"] == []
    store.close()


async def test_private_approval_resolution_stays_private(tmp_path):
    from hollywood_room.history import HistoryQuery, history_page

    store = Store(tmp_path / "room.db")
    approvals = Approvals(store)
    pending = asyncio.create_task(
        approvals.request(
            "item/commandExecution/requestApproval",
            {"threadId": "thread", "turnId": "turn", "command": "private-command"},
            task="lobby",
            agent="maya",
            visibility="direct:maya",
        )
    )
    await asyncio.sleep(0)
    event = store.snapshot()["attention"][0]["id"]
    try:
        approvals.answer(event, "accept")
        assert await pending == {"decision": "accept"}
        assert history_page(store, HistoryQuery())["data"] == []
        assert history_page(store, HistoryQuery(visibility="direct:theo"))["data"] == []
        private = history_page(store, HistoryQuery(visibility="direct:maya"))["data"]
        assert {item["kind"] for item in private} == {"approval", "resolved"}
    finally:
        store.close()
