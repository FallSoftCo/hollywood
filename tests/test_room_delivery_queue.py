import asyncio
from unittest.mock import AsyncMock

import pytest

from hollywood_room.rpc import RpcError
from hollywood_room.runtime import Runtime
from hollywood_room.store import Store


def test_startup_recovers_only_known_undelivered_budget_failures(tmp_path):
    path = tmp_path / "room.db"
    store = Store(path)
    budget_error = "Automatic turn budget reached; human review required"
    fixtures = [
        ("uncertain", None, budget_error),
        ("uncertain", None, "connection lost"),
        ("uncertain", None, budget_error + " (RPC)"),
        ("uncertain", "turn-a", budget_error),
        ("confirmed", "turn-b", budget_error),
        ("dispatching", None, budget_error),
    ]
    for index, (state, turn, error) in enumerate(fixtures):
        event = store.message(
            str(index),
            task="lobby",
            author="coordinator",
            body="Follow up",
            targets=["maya"],
            authority="tool",
        )
        with store.db:
            store.db.execute(
                "UPDATE delivery SET state=?,turn=?,error=? WHERE event=?",
                (state, turn, error, event["id"]),
            )
    store.close()
    store = Store(path)
    try:
        expected = [
            {"state": "queued", "turn": None, "error": None},
            *[
                {"state": state, "turn": turn, "error": error}
                for state, turn, error in fixtures[1:5]
            ],
            {
                "state": "uncertain",
                "turn": None,
                "error": "Runtime stopped during dispatch; delivery unconfirmed",
            },
        ]
        assert store.rows("SELECT state,turn,error FROM delivery ORDER BY id") == expected
        # A recovered delivery confirmed by the dispatcher must never be queued again.
        with store.db:
            store.db.execute("UPDATE delivery SET state='confirmed',turn='recovered' WHERE id=1")
        expected[0] = {"state": "confirmed", "turn": "recovered", "error": None}
        store.close()
        store = Store(path)
        assert store.rows("SELECT state,turn,error FROM delivery ORDER BY id") == expected
    finally:
        store.close()


@pytest.mark.parametrize(
    "authority,turns,depth", [("tool", 40, 0), ("tool", 0, 5), ("userReference", 40, 5)]
)
async def test_long_running_work_waits_for_capacity_then_delivers(
    tmp_path, authority, turns, depth
):
    store = Store(tmp_path / "room.db")
    store.message(
        "followup",
        task="lobby",
        author="coordinator",
        body="Continue the review",
        targets=["maya"],
        authority=authority,
        depth=depth,
    )
    with store.db:
        store.db.execute("UPDATE binding SET turns=?", (turns,))
    runtime = Runtime(store, binary="unused", home=tmp_path, state=tmp_path, workspace=tmp_path)
    runtime.ready = True
    limit = 0
    runtime.capacity.snapshot = lambda active: {"limit": limit}
    runtime.thread = AsyncMock(return_value="thread-a")
    runtime.rpc.call = AsyncMock(return_value={"turn": {"id": "turn-a"}})
    poller = asyncio.create_task(runtime.poll())
    try:
        await asyncio.sleep(0.12)
        assert store.rows("SELECT state FROM delivery") == [{"state": "queued"}]
        runtime.rpc.call.assert_not_awaited()
        limit = 1
        for _ in range(50):
            if store.rows("SELECT state FROM delivery") == [{"state": "confirmed"}]:
                break
            await asyncio.sleep(0.02)
        assert store.rows("SELECT state,turn,error FROM delivery") == [
            {"state": "confirmed", "turn": "turn-a", "error": None}
        ]
        assert store.rows("SELECT * FROM attention") == []
        runtime.rpc.call.assert_awaited_once()
        method, params = runtime.rpc.call.call_args.args
        assert method == "turn/start"
        assert ("toolOutput" in params) == (authority == "tool")
    finally:
        runtime.stopping = True
        poller.cancel()
        await asyncio.gather(poller, *runtime.dispatchers.values(), return_exceptions=True)
        store.close()


async def test_transport_uncertainty_is_not_automatically_retried(tmp_path):
    store = Store(tmp_path / "room.db")
    store.message(
        "followup",
        task="lobby",
        author="coordinator",
        body="Continue the review",
        targets=["maya"],
        authority="tool",
        depth=5,
    )
    runtime = Runtime(store, binary="unused", home=tmp_path, state=tmp_path, workspace=tmp_path)
    runtime.ready = True
    runtime.capacity.snapshot = lambda active: {"limit": 1}
    runtime.thread = AsyncMock(return_value="thread-a")
    runtime.rpc.call = AsyncMock(side_effect=RpcError("connection lost"))
    poller = asyncio.create_task(runtime.poll())
    try:
        await asyncio.sleep(0.25)
        assert store.rows("SELECT state,error FROM delivery") == [
            {"state": "uncertain", "error": "connection lost"}
        ]
        runtime.rpc.call.assert_awaited_once()
        assert store.rows("SELECT * FROM attention WHERE state='open'") == []
        assert len(store.rows("SELECT * FROM event WHERE kind='deliveryFailure'")) == 1
    finally:
        runtime.stopping = True
        poller.cancel()
        await asyncio.gather(poller, *runtime.dispatchers.values(), return_exceptions=True)
        store.close()
