import sqlite3
from types import SimpleNamespace

import pytest

from hollywood_room.conversations import Conversations
from hollywood_room.runtime import Runtime
from hollywood_room.store import Conflict, Store


async def test_existing_thread_survives_new_resume_and_restart(tmp_path):
    store = Store(tmp_path / "room.db")
    with store.db:
        store.ensure_binding("lobby", "maya")
        store.db.execute("UPDATE binding SET thread='original',cursor=27 WHERE agent='maya'")
    runtime = SimpleNamespace(store=store, active={}, dispatchers={}, retiring={})
    registry = Conversations(runtime)
    original = registry.list("lobby", "maya", "default")["data"][0]
    result = await registry.command("new", "new", "default", task="lobby", agent="maya")
    assert result == await registry.command("new", "new", "default", task="lobby", agent="maya")
    assert len(registry.list("lobby", "maya", "default")["data"]) == 2
    registry = Conversations(runtime)
    await registry.command("resume", "resume", "default", identifier=original["id"])
    binding = store.rows("SELECT * FROM binding WHERE agent='maya'")[0]
    assert (binding["thread"], binding["cursor"]) == ("original", 27)
    store.close()
    reopened = Store(tmp_path / "room.db")
    runtime.store = reopened
    registry = Conversations(runtime)
    assert reopened.db.execute("PRAGMA user_version").fetchone()[0] == 3
    page = registry.list("lobby", "maya", "default")
    assert len(page["data"]) == 2
    assert page["currentConversationId"] == original["id"]
    reopened.close()


async def test_detach_keeps_work_and_new_rejects_active_or_queued(tmp_path):
    store = Store(tmp_path / "room.db")
    with store.db:
        store.ensure_binding("lobby", "maya")
    runtime = SimpleNamespace(store=store, active={("lobby", "maya"): {}}, dispatchers={}, retiring={})
    registry = Conversations(runtime)
    original = registry.list("lobby", "maya", "default")["data"][0]
    await registry.command("detach", "detach", "default", identifier=original["id"])
    assert ("lobby", "maya") in runtime.active
    with pytest.raises(Conflict, match="active turn"):
        await registry.command("new", "new", "default", task="lobby", agent="maya")
    with pytest.raises(ValueError, match="this team"):
        registry.get(original["id"], "other")
    runtime.active.clear()
    store.message("message", task="lobby", author="you", body="hello", targets=["maya"], authority="user")
    with pytest.raises(Conflict, match="pending deliveries"):
        await registry.command("new", "new", "default", task="lobby", agent="maya")
    with store.db:
        store.db.execute("UPDATE delivery SET state='uncertain'")
    with pytest.raises(Conflict, match="pending deliveries"):
        await registry.command("new", "new", "default", task="lobby", agent="maya")


async def test_receipt_write_failure_rolls_back_new_and_selection(tmp_path):
    store = Store(tmp_path / "room.db")
    with store.db:
        store.ensure_binding("lobby", "maya")
    runtime = SimpleNamespace(store=store, active={}, dispatchers={}, retiring={})
    registry = Conversations(runtime)
    before = registry.list("lobby", "maya", "default")
    store.db.execute("CREATE TRIGGER fail_receipt BEFORE INSERT ON conversation_command BEGIN SELECT RAISE(ABORT,'fixture failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        await registry.command("new", "new", "default", task="lobby", agent="maya")
    assert registry.list("lobby", "maya", "default") == before


async def test_late_old_completion_cannot_finish_new_selected_conversation(tmp_path):
    store = Store(tmp_path / "room.db")
    with store.db:
        store.ensure_binding("lobby", "maya")
        store.db.execute("UPDATE binding SET thread='old-thread' WHERE agent='maya'")
    runtime = Runtime(store, binary="unused", home=tmp_path, state=tmp_path, workspace=tmp_path)
    registry = Conversations(runtime)
    await registry.command("new", "new", "default", task="lobby", agent="maya")
    with store.db:
        store.db.execute("UPDATE binding SET thread='new-thread',turn='new-turn',status='working' WHERE agent='maya'")
    await runtime.event("turn/completed", {"threadId": "old-thread", "turn": {"id": "old-turn", "status": "completed"}})
    binding = store.rows("SELECT thread,turn,status FROM binding WHERE agent='maya'")[0]
    assert binding == {"thread": "new-thread", "turn": "new-turn", "status": "working"}


async def test_resume_current_unpauses_queued_work_without_retarget(tmp_path):
    store = Store(tmp_path / "room.db")
    runtime = Runtime(store, binary="unused", home=tmp_path, state=tmp_path, workspace=tmp_path)
    store.message("message", task="lobby", author="you", body="hello", targets=["maya"], authority="user")
    with store.db:
        store.db.execute("UPDATE binding SET paused=1,thread='original' WHERE agent='maya'")
    registry = Conversations(runtime)
    identifier = registry.list("lobby", "maya", "default")["currentConversationId"]
    result = await registry.command("resume", "resume", "default", identifier=identifier)
    assert result["action"] == "resume"
    assert store.rows("SELECT paused,thread FROM binding WHERE agent='maya'")[0] == {"paused": 0, "thread": "original"}
    assert store.rows("SELECT state FROM delivery")[0]["state"] == "queued"
