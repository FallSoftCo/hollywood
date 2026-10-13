"""Model policy applies to all next turns without replacing conversations."""

from unittest.mock import AsyncMock

import pytest

from hollywood_room.runtime import Runtime
from hollywood_room.store import Store


@pytest.mark.parametrize("thread_state", ["new", "resumed", "loaded"])
@pytest.mark.parametrize("new_teammate", [False, True])
async def test_defaults_reach_each_next_turn_preserving_history(
    tmp_path, thread_state, new_teammate
):
    store = Store(tmp_path / "room.db")
    agent = "new-reviewer" if new_teammate else "maya"
    if new_teammate:
        store.add_teammate(agent, "default", "Review changes")
    event = store.message(
        "human", task="lobby", author="you", body="Review this",
        targets=[agent], authority="user",
    )
    before = store.get_event(event["id"])
    runtime = Runtime(
        store, binary="unused", home=tmp_path, state=tmp_path, workspace=tmp_path,
    )
    runtime.workspaces.for_agent = AsyncMock(return_value=tmp_path)
    if thread_state != "new":
        with store.db:
            store.db.execute(
                "UPDATE binding SET thread='existing-thread' WHERE task='lobby' AND agent=?",
                (agent,),
            )
    if thread_state == "loaded":
        runtime.loaded.add("existing-thread")

    async def rpc(method, params):
        if method == "thread/start":
            return {"thread": {"id": "new-thread"}}
        if method == "thread/resume":
            return {"thread": {"id": "existing-thread"}}
        assert method == "turn/start"
        return {"turn": {"id": "next-turn"}}

    runtime.rpc.call = AsyncMock(side_effect=rpc)
    delivery = store.rows("SELECT * FROM delivery WHERE event=?", (event["id"],))[0]
    try:
        await runtime.dispatch(delivery)
        calls = [call.args for call in runtime.rpc.call.await_args_list]
        expected = "new-thread" if thread_state == "new" else "existing-thread"
        if thread_state == "loaded":
            assert len(calls) == 1
        else:
            method, params = calls[0]
            assert method == ("thread/start" if thread_state == "new" else "thread/resume")
            assert params["model"] == "gpt-5.5"
            assert params["config"]["model_reasoning_effort"] == "low"
            if thread_state == "resumed":
                assert params["threadId"] == expected
        method, params = calls[-1]
        assert method == "turn/start"
        assert params["threadId"] == expected
        assert params["model"] == "gpt-5.5"
        assert params["effort"] == "low"
        assert "collaborationMode" not in params
        assert store.get_event(event["id"]) == before
        assert store.rows(
            "SELECT thread FROM binding WHERE task='lobby' AND agent=?", (agent,)
        ) == [{"thread": expected}]
        assert store.rows("SELECT state FROM delivery WHERE id=?", (delivery["id"],)) == [
            {"state": "confirmed"}
        ]
    finally:
        store.close()


async def test_active_steering_does_not_restart_or_change_inflight_turn(tmp_path):
    store = Store(tmp_path / "room.db")
    event = store.message(
        "human", task="lobby", author="you", body="Additional context",
        targets=["maya"], authority="user",
    )
    with store.db:
        store.db.execute("UPDATE binding SET thread='old-thread',turn='old-turn'")
    runtime = Runtime(
        store, binary="unused", home=tmp_path, state=tmp_path, workspace=tmp_path,
    )
    runtime.active[("lobby", "maya")] = {"visibility": event["visibility"]}
    runtime.rpc.call = AsyncMock(return_value={})
    delivery = store.rows("SELECT * FROM delivery WHERE event=?", (event["id"],))[0]
    try:
        await runtime.dispatch(delivery)
        runtime.rpc.call.assert_awaited_once()
        method, params = runtime.rpc.call.call_args.args
        assert method == "turn/steer"
        assert params["expectedTurnId"] == "old-turn"
        assert "model" not in params
        assert "effort" not in params
        assert store.rows("SELECT thread,turn FROM binding") == [
            {"thread": "old-thread", "turn": "old-turn"}
        ]
    finally:
        store.close()
