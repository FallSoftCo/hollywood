from types import SimpleNamespace

import pytest

from hollywood_room.limits import Limits
from hollywood_room.store import Conflict, Store


def setup(tmp_path, used=99):
    store = Store(tmp_path / "room.db")
    (tmp_path / "auth.json").write_text('{"tokens":{"account_id":"fixture"}}')
    calls = []
    state = {"used": used, "timeout": False, "reset": 10000}

    async def call(method, params):
        calls.append((method, params))
        if method == "account/read":
            return {"account": {"type": "chatgpt", "email": "fixture@example.test"}}
        if method == "account/rateLimits/read":
            return {"ordinaryUsageAllowed": True,
                    "rateLimits": {"primary": {"usedPercent": state["used"], "resetsAt": state["reset"]}},
                    "rateLimitResetCredits": {"availableCount": 2}}
        if state["timeout"]:
            raise TimeoutError()
        return {"outcome": "reset"}

    runtime = SimpleNamespace(store=store, rpc=SimpleNamespace(home=tmp_path, call=call))
    return Limits(runtime, clock=lambda: 1000), state, calls


async def test_strict_threshold_and_no_loop_without_recovery(tmp_path):
    limits, state, calls = setup(tmp_path, 98)
    await limits.automatic()
    assert not any("consume" in method for method, _ in calls)
    state["used"] = 99
    result = await limits.automatic()
    assert result["limits"]["remainingPercent"] == 1  # Never fabricate full recovery.
    await limits.automatic()
    assert sum("consume" in method for method, _ in calls) == 1
    state["reset"] = 20000
    limits = Limits(limits.runtime, clock=lambda: 1000)
    await limits.automatic()
    assert sum("consume" in method for method, _ in calls) == 1
    state["used"] = 1
    await limits.automatic()
    state["used"] = 99
    await limits.automatic()
    assert sum("consume" in method for method, _ in calls) == 2


async def test_timeout_durable_dedup_and_credential_change(tmp_path):
    limits, state, calls = setup(tmp_path)
    expected = (await limits.read())["accountKey"]
    state["timeout"] = True
    with pytest.raises(TimeoutError):
        await limits.reset("one", expected_account_key=expected)
    with pytest.raises(Conflict, match="earlier reset"):
        await limits.reset("two", expected_account_key=expected)
    with pytest.raises(Conflict, match="backoff"):
        await limits.reset("one", expected_account_key=expected)
    limits.clock = lambda: 1100
    state["timeout"] = False
    await limits.reset("one", expected_account_key=expected)
    await limits.reset("one", expected_account_key=expected)
    assert [params for method, params in calls if "consume" in method] == [
        {"idempotencyKey": "one"}, {"idempotencyKey": "one"}]
    (tmp_path / "auth.json").write_text('{"tokens":{"account_id":"changed"}}')
    with pytest.raises(Conflict, match="displayed eligibility"):
        await limits.reset("one", expected_account_key=expected)


async def test_missing_identity_fails_closed_without_consume(tmp_path):
    limits, _, calls = setup(tmp_path)
    expected = (await limits.read())["accountKey"]
    (tmp_path / "auth.json").unlink()
    with pytest.raises(ValueError, match="verifiable"):
        await limits.reset("one", expected_account_key=expected)
    assert not any("consume" in method for method, _ in calls)


async def test_manual_requires_displayed_account_before_consuming(tmp_path):
    limits, _, calls = setup(tmp_path)
    with pytest.raises(Conflict, match="displayed eligibility"):
        await limits.reset("one", expected_account_key="old-account")
    with pytest.raises(Conflict, match="displayed eligibility"):
        await limits.reset("one")
    assert not any("consume" in method for method, _ in calls)


async def test_account_change_during_read_and_slow_observation_fail_closed(tmp_path):
    limits, _, calls = setup(tmp_path)
    original = limits.runtime.rpc.call

    async def changing(method, params):
        result = await original(method, params)
        if method == "account/rateLimits/read":
            (tmp_path / "auth.json").write_text('{"tokens":{"account_id":"other"}}')
        return result

    limits.runtime.rpc.call = changing
    with pytest.raises(Conflict, match="changed while"):
        await limits.read()
    limits.runtime.rpc.call = original
    ticks = iter([1000, 1031])
    limits.clock = lambda: next(ticks)
    with pytest.raises(Conflict, match="expired"):
        await limits.read()
    assert not any("consume" in method for method, _ in calls)


async def test_automatic_uncertain_retry_reuses_key_after_restart(tmp_path):
    limits, state, calls = setup(tmp_path)
    state["timeout"] = True
    with pytest.raises(TimeoutError):
        await limits.automatic()
    await limits.automatic()
    limits = Limits(limits.runtime, clock=lambda: 1100)
    state["timeout"] = False
    await limits.automatic()
    consumes = [params for method, params in calls if "consume" in method]
    assert len(consumes) == 2
    assert consumes[0] == consumes[1]


@pytest.mark.parametrize("enabled", [True, False])
async def test_runtime_poll_respects_automatic_credit_reset_setting(tmp_path, monkeypatch, enabled):
    from unittest.mock import AsyncMock
    from hollywood_room.runtime import Runtime

    store = Store(tmp_path / "runtime.db")
    try:
        runtime = Runtime(store, binary="unused", home=tmp_path, state=tmp_path,
                          workspace=tmp_path, automatic_credit_reset=enabled)
        runtime.ready = True
        runtime.limits.automatic = AsyncMock()
        runtime.limits.read = AsyncMock()

        async def stop_after_one_poll(_):
            runtime.stopping = True

        monkeypatch.setattr("hollywood_room.runtime.asyncio.sleep", stop_after_one_poll)
        await runtime.poll_limits()
        assert runtime.limits.automatic.await_count == int(enabled)
        assert runtime.limits.read.await_count == int(not enabled)
    finally:
        store.close()
