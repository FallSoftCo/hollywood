"""Independent association checks; no real Codex thread is read or resumed."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from hollywood_room.external_threads import ExternalThreads
from hollywood_room.runtime import Runtime
from hollywood_room.store import Conflict, Store

THREAD = "00000000-0000-0000-0000-000000000042"


@pytest.fixture
def external(tmp_path):
    store = Store(tmp_path / "room.db")
    with store.db:
        store.ensure_binding("lobby", "maya")
        store.ensure_binding("lobby", "theo")
        store.event(
            "message", "lobby", "maya", "Existing private history", visibility="direct:maya"
        )
    runtime = Runtime(store, binary="unused", home=tmp_path, state=tmp_path, workspace=tmp_path)
    thread = {
        "id": THREAD,
        "name": "External",
        "status": {"type": "active"},
        "preview": "Never publish this preview",
        "turns": ["Never copy history"],
    }
    runtime.rpc.call = AsyncMock(return_value={"thread": thread})
    yield SimpleNamespace(
        store=store,
        runtime=runtime,
        registry=ExternalThreads(runtime),
        thread=thread,
        root=tmp_path,
    )
    store.close()


async def test_association_does_not_change_history_binding_or_acquire_writer(external):
    h = external
    before = h.store.rows("SELECT * FROM binding")
    history = h.store.events(visibility="direct:maya")
    result = await h.registry.associate(
        THREAD, "observe", h.registry.owner_id, "lobby", "maya", "default", "associate"
    )
    assert result["association"]["mode"] == "metadata"
    assert result["canAttachWriter"] is False
    assert h.store.rows("SELECT * FROM binding") == before
    assert h.store.events(visibility="direct:maya") == history
    h.runtime.rpc.call.assert_awaited_once_with(
        "thread/read", {"threadId": THREAD, "includeTurns": False}
    )
    metadata = await h.registry.read(THREAD, "lobby", "maya", "default")
    assert metadata["associated"] is True
    assert metadata["status"] == {"type": "active"}
    assert not {"preview", "turns"} & metadata.keys()


async def test_concurrent_different_agent_claims_have_one_metadata_owner(external):
    h = external
    both_reading = asyncio.Event()
    entered = 0

    async def read(method, params):
        nonlocal entered
        assert method == "thread/read"
        entered += 1
        if entered == 2:
            both_reading.set()
        await asyncio.wait_for(both_reading.wait(), 2)
        return {"thread": h.thread}

    h.runtime.rpc.call = read
    results = await asyncio.gather(
        *(
            h.registry.associate(
                THREAD,
                f"claim-{agent}",
                h.registry.owner_id,
                "lobby",
                agent,
                "default",
                "associate",
            )
            for agent in ("maya", "theo")
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(value, dict) for value in results) == 1
    assert sum(isinstance(value, (ValueError, Conflict)) for value in results) == 1
    assert len(h.store.rows("SELECT * FROM external_association")) == 1
    assert len(h.store.rows("SELECT * FROM external_command")) == 1


async def test_other_agent_is_filtered_and_cannot_remove_association(external):
    h = external
    await h.registry.associate(
        THREAD, "associate", h.registry.owner_id, "lobby", "maya", "default", "associate"
    )
    h.runtime.rpc.call.reset_mock()
    with pytest.raises(ValueError):
        await h.registry.read(THREAD, "lobby", "theo", "default")
    with pytest.raises(Conflict):
        await h.registry.associate(
            THREAD, "remove", h.registry.owner_id, "lobby", "theo", "default", "dissociate"
        )
    h.runtime.rpc.call.assert_not_awaited()
    h.runtime.rpc.call.return_value = {"data": [h.thread], "nextCursor": "source-next-page"}
    page = await h.registry.list("lobby", "theo", "default", limit=1)
    assert page["data"] == []
    assert page["nextCursor"] == "source-next-page"
    assert page["canAttachWriter"] is False


async def test_recreated_registry_replays_but_changed_home_rejects_original_command(external):
    h = external
    result = await h.registry.associate(
        THREAD, "original", h.registry.owner_id, "lobby", "maya", "default", "associate"
    )
    recreated = ExternalThreads(h.runtime)
    h.runtime.rpc.call.reset_mock()
    assert recreated.owner_id == h.registry.owner_id
    assert (
        await recreated.associate(
            THREAD, "original", recreated.owner_id, "lobby", "maya", "default", "associate"
        )
        == result
    )
    h.runtime.rpc.call.assert_not_awaited()
    h.runtime.rpc.home = h.root / "different-home"
    changed = ExternalThreads(h.runtime)
    assert changed.owner_id != recreated.owner_id
    with pytest.raises(Conflict, match="owner changed"):
        await changed.associate(
            THREAD, "original", recreated.owner_id, "lobby", "maya", "default", "associate"
        )
    h.runtime.rpc.call.assert_not_awaited()
    assert h.store.rows("SELECT owner,thread FROM external_association") == [
        {"owner": recreated.owner_id, "thread": THREAD}
    ]
