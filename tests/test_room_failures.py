from unittest.mock import AsyncMock

import pytest

from hollywood_room.failures import present_event
from hollywood_room.history import HistoryQuery, history_page
from hollywood_room.rpc import RpcError
from hollywood_room.runtime import Runtime
from hollywood_room.store import Store


@pytest.mark.parametrize("phase", ["preparingThread", "startingTurn", "steeringTurn"])
async def test_failure_contains_scoped_action_and_safe_retryability(tmp_path, phase):
    store = Store(tmp_path / "room.db")
    source = store.message(
        "human-action",
        task="lobby",
        author="you",
        body="Review the private draft",
        targets=["maya"],
        authority="user",
        visibility="direct:maya",
    )
    delivery = store.rows("SELECT * FROM delivery WHERE event=?", (source["id"],))[0]
    runtime = Runtime(store, binary="unused", home=tmp_path, state=tmp_path, workspace=tmp_path)
    runtime.thread = AsyncMock(return_value="thread-a")
    runtime.rpc.call = AsyncMock(side_effect=RpcError("Connection lost"))
    if phase == "preparingThread":
        runtime.thread.side_effect = OSError("Workspace unavailable")
    elif phase == "steeringTurn":
        runtime.active[("lobby", "maya")] = {"visibility": "direct:maya"}
        with store.db:
            store.db.execute("UPDATE binding SET thread='thread-a',turn='turn-a'")
    try:
        await runtime.dispatch(delivery)
        failure = store.get_event(
            store.rows("SELECT id FROM event WHERE kind='deliveryFailure'")[0]["id"]
        )
        assert store.snapshot()["attention"] == []
        assert store.rows("SELECT * FROM push_outbox") == []
        details = failure["data"]["deliveryFailure"]
        assert details["sourcePreview"] == "Review the private draft"
        assert details["commandId"] == "human-action"
        assert details["sourceEventId"] == source["id"]
        assert details["deliveryId"] == delivery["id"]
        assert details["recipient"] == "maya"
        assert details["teamId"] == "default"
        assert details["taskTitle"] == "Project room"
        assert details["phase"] == phase
        assert details["retryable"] == (phase == "preparingThread")
        assert failure["visibility"] == "direct:maya"
        assert "private draft" in failure["body"]
        assert history_page(store, HistoryQuery())["data"] == []
        assert history_page(store, HistoryQuery(visibility="direct:theo"))["data"] == []
        assert len(store.rows("SELECT * FROM delivery")) == 1
        with store.db:
            store.db.execute("UPDATE delivery SET state='confirmed' WHERE id=?", (delivery["id"],))
        recovered = present_event(store, failure)
        assert recovered["data"]["deliveryFailure"]["state"] == "confirmed"
        assert recovered["data"]["deliveryFailure"]["retryable"] is False
        assert "delivered after recovery" in recovered["body"]
    finally:
        store.close()


def test_legacy_unique_match_is_enriched_but_ambiguous_ids_not_guessed(tmp_path):
    store = Store(tmp_path / "room.db")
    try:
        source = store.message(
            "first",
            task="lobby",
            author="you",
            body="Inspect build",
            targets=["maya"],
            authority="user",
        )
        with store.db:
            store.db.execute("UPDATE delivery SET state='uncertain',error='Connection lost'")
        alert = store.attention("lobby", "maya", "Could not confirm delivery: Connection lost")
        event = store.get_event(alert)
        value = present_event(store, event)["data"]["deliveryFailure"]
        assert value["sourceEventId"] == source["id"]
        assert value["retryable"] is False
        assert store.get_event(alert) == event  # enrichment never rewrites history
        second = store.message(
            "second",
            task="lobby",
            author="you",
            body="Other action",
            targets=["maya"],
            authority="user",
        )
        with store.db:
            store.db.execute(
                "UPDATE delivery SET state='uncertain',error='Connection lost' WHERE event=?",
                (second["id"],),
            )
        ambiguous = store.attention("lobby", "maya", "Could not confirm delivery: Connection lost")
        value = present_event(store, store.get_event(ambiguous))["data"]["deliveryFailure"]
        assert value["deliveryId"] is None
        assert value["sourcePreview"] is None
        assert value["state"] == "unknown"
        assert value["recipient"] == "maya"
    finally:
        store.close()


def test_legacy_public_failure_does_not_leak_private_source(tmp_path):
    store = Store(tmp_path / "room.db")
    try:
        store.message(
            "private",
            task="lobby",
            author="you",
            body="Sensitive private draft",
            targets=["maya"],
            authority="user",
            visibility="direct:maya",
        )
        with store.db:
            store.db.execute("UPDATE delivery SET state='uncertain',error='Connection lost'")
        alert = store.attention("lobby", "maya", "Could not confirm delivery: Connection lost")
        value = present_event(store, store.get_event(alert))
        assert value["data"]["deliveryFailure"]["sourcePreview"] is None
        assert "Sensitive" not in value["body"]
    finally:
        store.close()
