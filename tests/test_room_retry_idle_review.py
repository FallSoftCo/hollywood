"""Independent fault injection for durable delivery retry boundaries."""

import asyncio
import time
from unittest.mock import AsyncMock

import pytest

from hollywood_room.retries import delay
from hollywood_room.idle import Idle, stale
from hollywood_room.rpc import RpcError
from hollywood_room.runtime import Runtime
from hollywood_room.store import Store


def enqueue(store, command="original", agent="maya"):
    store.message(command, task="lobby", author="coordinator", body=command,
                  targets=[agent], authority="tool")
    return store.rows("SELECT * FROM delivery ORDER BY id DESC")[0]


def runtime_for(store, tmp_path):
    runtime = Runtime(store, binary="unused", home=tmp_path, state=tmp_path,
                      workspace=tmp_path)
    runtime.ready = True
    runtime.capacity.snapshot = lambda active: {"limit": 4}
    runtime.thread = AsyncMock(return_value="thread-original")
    runtime.rpc.call = AsyncMock(return_value={"turn": {"id": "accepted"}})
    return runtime


def test_retry_jitter_bounds_remain_capped_without_attempt_cutoff():
    for attempt in [0, 1, 8, 9, 100, 1000000]:
        low = delay(attempt, uniform=lambda low, high: low)
        high = delay(attempt, uniform=lambda low, high: high)
        assert 0 < low == high / 2 <= 150
        assert high <= 300


@pytest.mark.parametrize("phase,expected", [
    (None, "uncertain"), ("preparingThread", "queued"),
    ("startingTurn", "uncertain"), ("steeringTurn", "uncertain"),
])
def test_restart_phase_fence_preserves_identity_and_deadline(tmp_path, phase, expected):
    path = tmp_path / "room.db"
    store = Store(path)
    original = enqueue(store)
    with store.db:
        store.db.execute("UPDATE delivery SET state='dispatching',phase=?,attempts=7,next_at=?",
                         (phase, 9999999999))
    store.close()
    store = Store(path)
    try:
        restored = store.rows("SELECT * FROM delivery")[0]
        assert restored["state"] == expected
        assert restored["attempts"] == 7
        assert restored["next_at"] == 9999999999
        for key in ["id", "event", "task", "agent", "authority"]:
            assert restored[key] == original[key]
    finally:
        store.close()


async def test_transient_retry_confirms_original_delivery_and_clears_failure(tmp_path):
    store = Store(tmp_path / "room.db")
    original = enqueue(store)
    runtime = runtime_for(store, tmp_path)
    runtime.thread.side_effect = [OSError("network unavailable"), "thread-original"]
    try:
        before = time.time()
        await runtime.dispatch(original)
        retry = store.rows("SELECT * FROM delivery")[0]
        assert retry["state"] == "queued"
        assert retry["attempts"] == 1 and retry["next_at"] > before
        runtime.rpc.call.assert_not_awaited()
        await runtime.dispatch(retry)
        result = store.rows("SELECT * FROM delivery")
        assert len(result) == 1
        assert result[0]["id"] == original["id"]
        assert result[0]["state"] == "confirmed"
        assert result[0]["error"] is None
        assert result[0]["turn"] == "accepted"
        assert store.rows("SELECT * FROM attention") == []
    finally:
        store.close()


async def test_delayed_binding_does_not_overtake_or_block_other_agent(tmp_path):
    store = Store(tmp_path / "room.db")
    first = enqueue(store)
    enqueue(store, "later-same-binding")
    other = enqueue(store, "other-binding", "theo")
    with store.db:
        store.db.execute("UPDATE delivery SET next_at=? WHERE id=?",
                         (time.time() + 3600, first["id"]))
    runtime = runtime_for(store, tmp_path)
    poller = asyncio.create_task(runtime.poll())
    try:
        await asyncio.sleep(0.25)
        states = {row["id"]: row["state"] for row in store.rows("SELECT * FROM delivery")}
        assert states == {first["id"]: "queued", first["id"] + 1: "queued", other["id"]: "confirmed"}
        runtime.rpc.call.assert_awaited_once()
    finally:
        runtime.stopping = True
        poller.cancel()
        await asyncio.gather(poller, *runtime.dispatchers.values(), return_exceptions=True)
        store.close()


async def test_accepted_response_lost_never_replays_tool_turn(tmp_path):
    store = Store(tmp_path / "room.db")
    original = enqueue(store)
    runtime = runtime_for(store, tmp_path)
    runtime.rpc.call.side_effect = RpcError("accepted remotely; response lost")
    await runtime.dispatch(original)
    poller = asyncio.create_task(runtime.poll())
    try:
        await asyncio.sleep(0.25)
        assert store.rows("SELECT state FROM delivery") == [{"state": "uncertain"}]
        runtime.rpc.call.assert_awaited_once()
    finally:
        runtime.stopping = True
        poller.cancel()
        await asyncio.gather(poller, *runtime.dispatchers.values(), return_exceptions=True)
        store.close()


@pytest.fixture
def idle_setup(tmp_path):
    store = Store(tmp_path / "room.db")
    clock = [0.0]
    idle = Idle(store, clock=lambda: clock[0], debounce=5, host="review-host")
    yield store, idle, clock
    store.close()


def idle_events(store):
    return [store.get_event(row["id"]) for row in
            store.rows("SELECT id FROM event WHERE kind='teamIdle'")]


def test_idle_startup_disconnect_and_repeated_scans_are_quiet(idle_setup):
    store, idle, clock = idle_setup
    for tick in [0, 10, 100]:
        clock[0] = tick
        idle.sample()
    assert idle_events(store) == []
    idle.sample([("lobby", "maya")])
    idle.sample()
    idle.disconnected()
    clock[0] += 100
    idle.sample()
    assert idle_events(store) == []
    idle.sample([("lobby", "maya")])
    idle.sample()
    clock[0] += 5
    idle.sample()
    for _ in range(10):
        clock[0] += 100
        idle.sample()
    assert len(idle_events(store)) == 1


@pytest.mark.parametrize("state", ["queued", "dispatching", "uncertain", "waiting_usage", "blocked"])
def test_pending_delivery_with_future_deadline_prevents_idle(idle_setup, state):
    store, idle, clock = idle_setup
    enqueue(store)
    with store.db:
        store.db.execute("UPDATE delivery SET state=?,next_at=9999999999", (state,))
    idle.sample()
    clock[0] = 100
    idle.sample()
    assert idle_events(store) == []


def test_idle_debounce_resets_on_work_and_questions_stay_distinct(idle_setup):
    store, idle, clock = idle_setup
    question = store.attention("lobby", "maya", "Choose an option")
    idle.sample([("lobby", "maya")])
    idle.sample()
    clock[0] = 4
    idle.sample([("lobby", "maya")])
    idle.sample()
    clock[0] = 8
    idle.sample()
    assert idle_events(store) == []
    clock[0] = 9
    idle.sample()
    event, = idle_events(store)
    assert event["data"]["hostName"] == "review-host"
    assert event["data"]["team_id"] == "default"
    assert store.rows("SELECT id,state FROM attention") == [{"id": question, "state": "open"}]
    assert store.rows("SELECT event FROM push_outbox WHERE event=?", (event["id"],))


def test_idle_teams_transition_independently_and_old_push_expires(idle_setup):
    store, idle, clock = idle_setup
    store.add_team("other", "Other", "lead-o", "Lead")
    idle.sample([("lobby", "maya"), ("team-other", "lead-o")])
    idle.sample([("lobby", "maya")])
    clock[0] = 5
    idle.sample([("lobby", "maya")])
    other, = idle_events(store)
    assert other["task"] == "team-other"
    assert other["data"]["team_id"] == "other"
    assert not stale(store, other)
    idle.sample()
    clock[0] = 10
    idle.sample()
    default = idle_events(store)[1]
    enqueue(store, "new-cycle")
    with store.db:
        store.db.execute("UPDATE delivery SET state='confirmed'")
    assert stale(store, default)
    assert not stale(store, other)


def test_idle_push_is_stale_if_an_old_confirmed_turn_is_running(idle_setup):
    store, idle, clock = idle_setup
    enqueue(store)
    with store.db:
        store.db.execute("UPDATE delivery SET state='confirmed'")
    idle.sample([("lobby", "maya")])
    idle.sample()
    clock[0] = 5
    idle.sample()
    event, = idle_events(store)
    # A runtime reconnect can rediscover an already-confirmed turn as running.
    # No new delivery event is necessary for that observation.
    with store.db:
        store.db.execute("UPDATE binding SET status='working',turn='old-running'")
    assert stale(store, event)


def uncertain_user(store):
    store.message("original-user", task="lobby", author="you", body="Original request",
                  targets=["maya"], authority="user", visibility="direct:maya")
    with store.db:
        store.db.execute("UPDATE binding SET thread='thread-original'")
        store.db.execute("UPDATE delivery SET state='uncertain',phase='startingTurn',error='lost response'")
    return store.rows("SELECT * FROM delivery")[0]


@pytest.mark.parametrize("status", ["completed", "inProgress"])
async def test_reconcile_user_receipt_matches_original_client_id(tmp_path, status):
    store = Store(tmp_path / "room.db")
    original = uncertain_user(store)
    runtime = runtime_for(store, tmp_path)
    runtime.rpc.call.return_value = {"thread": {"turns": [
        {"id": "accepted", "status": status, "items": [
            {"type": "userMessage", "clientId": f"hollywood-{original['id']}"}]}]}}
    try:
        await runtime.reconcile()
        receipt = store.rows("SELECT * FROM delivery")[0]
        assert receipt["id"] == original["id"]
        assert receipt["state"] == "confirmed" and receipt["turn"] == "accepted"
        assert receipt["error"] is None
        assert (("lobby", "maya") in runtime.active) == (status == "inProgress")
        assert runtime.rpc.call.call_args.args[0] == "thread/read"
    finally:
        store.close()


@pytest.mark.parametrize("response", [
    {"thread": {"turns": []}},
    {"thread": {"turns": [{"id": "unrelated", "status": "completed", "items": [
        {"type": "userMessage", "clientId": "hollywood-wrong"}]}]}},
])
async def test_reconcile_absent_or_foreign_receipt_never_replays(tmp_path, response):
    store = Store(tmp_path / "room.db")
    uncertain_user(store)
    runtime = runtime_for(store, tmp_path)
    runtime.rpc.call.return_value = response
    try:
        await runtime.reconcile()
        row = store.rows("SELECT * FROM delivery")[0]
        assert row["state"] == "uncertain" and row["next_at"] > time.time()
        await runtime.reconcile()
        runtime.rpc.call.assert_awaited_once()
        assert runtime.rpc.call.call_args.args[0] == "thread/read"
    finally:
        store.close()


async def test_reconcile_running_receipt_resume_failure_remains_recoverable(tmp_path):
    store = Store(tmp_path / "room.db")
    original = uncertain_user(store)
    runtime = runtime_for(store, tmp_path)
    runtime.rpc.call.return_value = {"thread": {"turns": [
        {"id": "accepted", "status": "inProgress", "items": [
            {"type": "userMessage", "clientId": f"hollywood-{original['id']}"}]}]}}
    runtime.thread.side_effect = [OSError("resume unavailable"), "thread-original"]
    try:
        try:
            await runtime.reconcile()
        except OSError:
            pass
        with store.db:
            store.db.execute("UPDATE delivery SET next_at=0")
        await runtime.reconcile()
        assert runtime.thread.await_count == 2
        assert store.rows("SELECT state,turn FROM delivery") == [
            {"state": "confirmed", "turn": "accepted"}]
    finally:
        store.close()


async def test_transport_rejects_before_write_is_safe_to_retry(tmp_path):
    store = Store(tmp_path / "room.db")
    original = enqueue(store)
    runtime = Runtime(store, binary="unused", home=tmp_path, state=tmp_path,
                      workspace=tmp_path)
    runtime.thread = AsyncMock(return_value="already-loaded-thread")
    # Real transport has no process: write() rejects before any submission bytes.
    assert runtime.rpc.process is None
    try:
        await runtime.dispatch(original)
        row = store.rows("SELECT * FROM delivery")[0]
        assert row["state"] == "queued"
        assert row["next_at"] > time.time()
        assert row["id"] == original["id"]
    finally:
        store.close()


async def test_push_worker_expires_idle_notice_when_team_resumes(idle_setup, monkeypatch):
    from firebase_admin import messaging
    from hollywood_room.push import Push

    store, idle, clock = idle_setup
    with store.db:
        store.db.execute("INSERT INTO device(id,token,after_event) VALUES('device','fixture-fid',0)")
    idle.sample([("lobby", "maya")])
    idle.sample()
    clock[0] = 5
    idle.sample()
    notice, = idle_events(store)
    enqueue(store, "resumed-work")
    sent = []
    monkeypatch.setattr(messaging, "send", lambda message, **kwargs: sent.append(message))
    push = object.__new__(Push)
    push.store, push.app = store, None
    worker = asyncio.create_task(push.run())
    try:
        await asyncio.sleep(0.25)
        assert sent == []
        assert store.rows("SELECT state FROM push_delivery WHERE event=?", (notice["id"],)) == [
            {"state": "expired"}]
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
