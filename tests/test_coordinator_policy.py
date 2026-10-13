from unittest.mock import AsyncMock

import pytest

from hollywood_room.runtime import Runtime
from hollywood_room.store import Store
from hollywood_room.tools import COORDINATOR_POLICY, instruction


@pytest.mark.parametrize("agent", ["coordinator", "maya", "theo", "rowan"])
@pytest.mark.parametrize("resumed", [False, True])
async def test_coordinator_policy_reaches_only_coordinator_threads(tmp_path, agent, resumed):
    store = Store(tmp_path / "room.db")
    try:
        store.ensure_binding("lobby", agent)
        if resumed:
            with store.db:
                store.db.execute("UPDATE binding SET thread='existing-thread'")
        runtime = Runtime(
            store, binary="unused", home=tmp_path, state=tmp_path,
            workspace=tmp_path, workspace_mode="shared",
        )
        runtime.rpc.call = AsyncMock(return_value={"thread": {"id": "new-thread"}})
        await runtime.thread("lobby", agent)
        method, params = runtime.rpc.call.call_args.args
        assert method == ("thread/resume" if resumed else "thread/start")
        prompt = params["developerInstructions"]
        assert instruction(agent, "lobby") in prompt
        assert (COORDINATOR_POLICY in prompt) == (agent == "coordinator")
        assert "shared development root" in prompt
    finally:
        store.close()
