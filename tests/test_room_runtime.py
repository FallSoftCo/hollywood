from unittest.mock import AsyncMock

from hollywood_room.runtime import Runtime
from hollywood_room.store import Store


async def test_retried_interrupt_cannot_interrupt_a_later_turn(tmp_path):
    store = Store(tmp_path / "room.db")
    runtime = Runtime(store, binary="unused", home=tmp_path, state=tmp_path, workspace=tmp_path)
    runtime.rpc.call = AsyncMock(return_value={})
    store.message(
        "start",
        task="lobby",
        author="you",
        body="Maya, inspect this",
        targets=["maya"],
        authority="user",
    )
    with store.db:
        store.db.execute("UPDATE binding SET thread='thread-a',turn='turn-a'")
    first = await runtime.control("lobby", "maya", "interrupt", command_id="stop-a")
    with store.db:
        store.db.execute("UPDATE binding SET turn='turn-b'")
    assert await runtime.control("lobby", "maya", "interrupt", command_id="stop-a") == first
    runtime.rpc.call.assert_awaited_once_with(
        "turn/interrupt", {"threadId": "thread-a", "turnId": "turn-a"}
    )
    store.close()


async def test_peer_delivery_uses_tool_authority_and_separate_thread(tmp_path):
    store = Store(tmp_path / "room.db")
    store.message(
        "human",
        task="lobby",
        author="you",
        body="Maya, investigate this",
        targets=["maya"],
        authority="user",
    )
    peer = store.message(
        "peer",
        task="lobby",
        author="maya",
        body="Theo, review the finding",
        targets=["theo"],
        authority="tool",
    )
    runtime = Runtime(store, binary="unused", home=tmp_path, state=tmp_path, workspace=tmp_path)
    runtime.thread = AsyncMock(return_value="theo-thread")
    runtime.rpc.call = AsyncMock(return_value={"turn": {"id": "theo-turn"}})
    delivery = store.rows("SELECT * FROM delivery WHERE event=?", (peer["id"],))[0]
    await runtime.dispatch(delivery)
    method, params = runtime.rpc.call.call_args.args
    assert method == "turn/start"
    assert params["threadId"] == "theo-thread"
    assert params["input"] == []
    assert params["toolOutput"]["namespace"] == "hollywood"
    assert "clientUserMessageId" not in params
    assert store.rows("SELECT agent,state FROM delivery ORDER BY id") == [
        {"agent": "maya", "state": "queued"},
        {"agent": "theo", "state": "confirmed"},
    ]
    store.close()
