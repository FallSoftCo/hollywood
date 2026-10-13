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


async def test_new_and_resumed_threads_use_full_access(tmp_path):
    store = Store(tmp_path / "room.db")
    store.message(
        "human", task="lobby", author="you", body="Review UX", targets=["maya"], authority="user"
    )
    runtime = Runtime(store, binary="unused", home=tmp_path, state=tmp_path, workspace=tmp_path)
    runtime.workspaces.for_agent = AsyncMock(return_value=tmp_path)
    runtime.rpc.call = AsyncMock(return_value={"thread": {"id": "thread-a"}})
    try:
        assert await runtime.thread("lobby", "maya") == "thread-a"
        method, params = runtime.rpc.call.call_args.args
        assert (method, params["sandbox"], params["approvalPolicy"]) == (
            "thread/start",
            "danger-full-access",
            "never",
        )
        runtime.loaded.clear()
        assert await runtime.thread("lobby", "maya") == "thread-a"
        method, params = runtime.rpc.call.call_args.args
        assert (method, params["sandbox"], params["approvalPolicy"]) == (
            "thread/resume",
            "danger-full-access",
            "never",
        )
    finally:
        store.close()


async def test_progress_and_actions_are_visible_without_internal_reasoning(tmp_path):
    from hollywood_room.history import HistoryQuery, history_page

    store = Store(tmp_path / "room.db")
    store.message(
        "human", task="lobby", author="you", body="Review UX", targets=["maya"], authority="user"
    )
    with store.db:
        store.db.execute("UPDATE binding SET thread='thread-a'")
    runtime = Runtime(store, binary="unused", home=tmp_path, state=tmp_path, workspace=tmp_path)
    runtime.active[("lobby", "maya")] = {"visibility": "direct:maya"}
    try:
        for item in [
            {
                "id": "note",
                "type": "agentMessage",
                "phase": "commentary",
                "text": "I’m checking the composer.",
            },
            {
                "id": "cmd",
                "type": "commandExecution",
                "command": "rg composer app",
                "status": "completed",
            },
            {"id": "thought", "type": "reasoning", "content": ["Private internal reasoning"]},
        ]:
            await runtime.event("item/completed", {"threadId": "thread-a", "item": item})
        page = history_page(store, HistoryQuery(visibility="direct:maya"))
        assert [(e["kind"], e["body"]) for e in page["data"]] == [
            ("artifact", "Ran command\nrg composer app"),
            ("agentNote", "I’m checking the composer."),
        ]
        assert all(e["kind"] == "message" for e in history_page(store, HistoryQuery())["data"])
    finally:
        store.close()


async def test_reply_and_completion_identify_original_delivery(tmp_path):
    store = Store(tmp_path / "room.db")
    request = store.message("recording-a", task="lobby", author="you",
                            body="Read status", targets=["maya"], authority="user")
    delivery = store.rows("SELECT * FROM delivery WHERE event=?", (request["id"],))[0]
    with store.db:
        store.db.execute("UPDATE binding SET thread='thread-a' WHERE agent='maya'")
    runtime = Runtime(store, binary="unused", home=tmp_path, state=tmp_path, workspace=tmp_path)
    runtime.active[("lobby", "maya")] = {"visibility": "room", "delivery": delivery}
    try:
        await runtime.event("item/completed", {"threadId": "thread-a", "turnId": "turn-a",
            "item": {"id": "answer-a", "type": "agentMessage", "phase": "final_answer", "text": "Verified status"}})
        await runtime.event("turn/completed", {"threadId": "thread-a",
            "turn": {"id": "turn-a", "status": "completed"}})
        events = store.events(after=request["id"], task="lobby")
        assert [e["kind"] for e in events] == ["message", "turn"]
        assert all(e["data"]["sourceEvent"] == request["id"] for e in events)
        assert all(e["data"]["turn"] == "turn-a" for e in events)
    finally:
        store.close()
