from hollywood_room.capacity import Capacity


def test_resource_budget_scales_past_four(monkeypatch):
    monkeypatch.setattr(Capacity, "resources", staticmethod(lambda: (32, 20 * 1024**3)))
    capacity = Capacity()
    assert capacity.snapshot()["limit"] == 19
    assert capacity.snapshot(5)["limit"] == 24


def test_pressure_stops_admissions_without_stopping_active(monkeypatch):
    monkeypatch.setattr(Capacity, "resources", staticmethod(lambda: (32, 100 * 1024**2)))
    capacity = Capacity()
    assert capacity.snapshot(3, 17) == {
        "limit": 3,
        "active": 3,
        "queued": 17,
        "reason": "memory",
        "availableMemoryBytes": 100 * 1024**2,
        "cpuSlots": 32,
    }


def test_operator_ceiling_and_cpu(monkeypatch):
    monkeypatch.setattr(Capacity, "resources", staticmethod(lambda: (8, None)))
    assert Capacity(max_turns=2).snapshot()["limit"] == 2
    assert Capacity().snapshot()["limit"] == 8


async def test_scheduler_admits_more_than_four_and_steers_at_capacity(tmp_path):
    import asyncio
    from unittest.mock import AsyncMock

    from hollywood_room.runtime import Runtime
    from hollywood_room.store import Store

    store = Store(tmp_path / "room.db")
    runtime = Runtime(store, binary="unused", home=tmp_path, state=tmp_path, workspace=tmp_path)
    runtime.ready = True
    runtime.capacity.snapshot = lambda active: {"limit": 6}
    seen = []

    async def dispatch(delivery):
        key = (delivery["task"], delivery["agent"])
        seen.append(key)
        runtime.active[key] = {}
        with store.db:
            store.db.execute("UPDATE delivery SET state='confirmed' WHERE id=?", (delivery["id"],))

    runtime.dispatch = AsyncMock(side_effect=dispatch)
    try:
        for index in range(8):
            store.add_task(f"task-{index}", "Task", None)
            store.message(
                str(index),
                task=f"task-{index}",
                author="you",
                body="Work",
                targets=["maya"],
                authority="user",
            )
        poller = asyncio.create_task(runtime.poll())
        await asyncio.sleep(0.03)
        assert len(seen) == 6
        assert len(store.rows("SELECT * FROM delivery WHERE state='queued'")) == 2
        store.message(
            "steer",
            task="task-0",
            author="you",
            body="Correction",
            targets=["maya"],
            authority="user",
        )
        await asyncio.sleep(0.12)
        assert seen.count(("task-0", "maya")) == 2
        runtime.active.pop(("task-1", "maya"))
        await asyncio.sleep(0.12)
        assert ("task-6", "maya") in seen
    finally:
        runtime.stopping = True
        poller.cancel()
        await asyncio.gather(poller, return_exceptions=True)
        store.close()


async def test_pending_starts_do_not_create_memory_headroom(tmp_path, monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock

    from hollywood_room.runtime import Runtime
    from hollywood_room.store import Store

    monkeypatch.setattr(Capacity, "resources", staticmethod(lambda: (32, 2 * 1024**3)))
    store = Store(tmp_path / "room.db")
    runtime = Runtime(store, binary="unused", home=tmp_path, state=tmp_path, workspace=tmp_path)
    runtime.ready = True
    hold = asyncio.Event()
    runtime.dispatch = AsyncMock(side_effect=lambda delivery: None)

    async def blocked(delivery):
        await hold.wait()

    runtime.dispatch = AsyncMock(side_effect=blocked)
    for index in range(10):
        store.add_task(f"t-{index}", "Work", None)
        store.message(
            str(index),
            task=f"t-{index}",
            author="you",
            body="Work",
            targets=["maya"],
            authority="user",
        )
    poller = asyncio.create_task(runtime.poll())
    try:
        await asyncio.sleep(0.35)
        assert len(runtime.dispatchers) == 1
        assert runtime.dispatch.await_count == 1
    finally:
        runtime.stopping = True
        poller.cancel()
        for job in runtime.dispatchers.values():
            job.cancel()
        await asyncio.gather(poller, *runtime.dispatchers.values(), return_exceptions=True)
        store.close()


def test_nested_cgroup_ancestor_quota_and_memory(monkeypatch):
    from pathlib import Path

    import hollywood_room.capacity as module

    files = {
        "/proc/meminfo": "MemAvailable: 67108864 kB",
        "/proc/self/cgroup": "0::/user.slice/app.scope",
        "/sys/fs/cgroup/cpu.max": "max 100000",
        "/sys/fs/cgroup/user.slice/cpu.max": "200000 100000",
        "/sys/fs/cgroup/user.slice/app.scope/cpu.max": "800000 100000",
        "/sys/fs/cgroup/user.slice/memory.max": str(4 * 1024**3),
        "/sys/fs/cgroup/user.slice/memory.current": str(3 * 1024**3),
    }

    def read(path, *args, **kwargs):
        if str(path) not in files:
            raise FileNotFoundError(str(path))
        return files[str(path)]

    monkeypatch.setattr(Path, "read_text", read)
    monkeypatch.setattr(module.os, "sched_getaffinity", lambda pid: set(range(32)))
    assert Capacity.resources() == (2, 1024**3)


async def test_completed_persistent_thread_unsubscribes_then_resumes(tmp_path):
    import asyncio
    from unittest.mock import AsyncMock

    from hollywood_room.runtime import Runtime
    from hollywood_room.store import Store

    store = Store(tmp_path / "room.db")
    store.message(
        "work", task="lobby", author="you", body="Work", targets=["maya"], authority="user"
    )
    with store.db:
        store.db.execute("UPDATE binding SET thread='thread-a',turn='turn-a'")
    runtime = Runtime(store, binary="unused", home=tmp_path, state=tmp_path, workspace=tmp_path)
    runtime.loaded.add("thread-a")
    runtime.active[("lobby", "maya")] = {"visibility": "room"}
    runtime.rpc.call = AsyncMock(return_value={"status": "unsubscribed"})
    runtime.workspaces.for_agent = AsyncMock(return_value=tmp_path)
    try:
        await runtime.event(
            "turn/completed",
            {"threadId": "thread-a", "turn": {"id": "turn-a", "status": "completed"}},
        )
        await asyncio.gather(*runtime.retiring.values())
        assert "thread-a" not in runtime.loaded
        runtime.rpc.call.assert_awaited_once_with("thread/unsubscribe", {"threadId": "thread-a"})
        assert await runtime.thread("lobby", "maya") == "thread-a"
        method, params = runtime.rpc.call.call_args.args
        assert method == "thread/resume"
        assert params["sandbox"] == "danger-full-access"
        assert params["approvalPolicy"] == "never"
    finally:
        store.close()
