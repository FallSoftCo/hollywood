import asyncio
from contextlib import suppress

import pytest
from aiohttp import ClientSession, web
from firebase_admin import messaging

from hollywood_room import presence
from hollywood_room.push import Push
from hollywood_room.server import Service, application
from hollywood_room.store import Store


@pytest.fixture
def store(tmp_path):
    value = Store(tmp_path / "room.db")
    with value.db:
        value.db.executemany(
            "INSERT INTO device(id,token) VALUES(?,?)",
            [("phone", "phone-token"), ("tablet", "tablet-token")],
        )
    yield value
    value.close()


def test_expiry_preference_revocation_and_no_heartbeat_turns(store):
    before = store.snapshot()["cursor"]
    value = presence.PresenceReport(environment="android", state="foreground")
    assert presence.report(store, "phone", value, now=100)["expiresAt"] == 190
    assert presence.suppress_push(store, "phone", now=189)
    assert not presence.suppress_push(store, "tablet", now=100)
    assert presence.device_state(store, "phone", now=190)["state"] == "unknown"
    assert not presence.suppress_push(store, "phone", now=99)
    presence.report(
        store, "phone", value.model_copy(update={"suppressForegroundPush": False}), now=200
    )
    presence.report(store, "phone", value, now=210)
    assert not presence.suppress_push(store, "phone", now=220)
    presence.report(
        store, "phone", value.model_copy(update={"suppressForegroundPush": True}), now=230
    )
    presence.report(store, "phone", value.model_copy(update={"state": "background"}), now=240)
    assert not presence.suppress_push(store, "phone", now=241)
    with store.db:
        store.db.execute("UPDATE device SET revoked=1 WHERE id='phone'")
    assert presence.device_state(store, "phone", now=241)["state"] == "unknown"
    with pytest.raises(ValueError, match="registered"):
        presence.report(store, "phone", value)
    assert store.snapshot()["cursor"] == before
    assert store.rows("SELECT * FROM delivery") == []


def test_agent_context_is_bounded_and_contains_no_identifiers(store):
    presence.report(
        store, "phone", presence.PresenceReport(environment="android", state="foreground"), now=100
    )
    assert "android foreground" in presence.agent_context(store, now=100)
    assert "android unknown" in presence.agent_context(store, now=190)
    assert "phone" not in presence.agent_context(store, now=100)
    assert "token" not in presence.agent_context(store, now=100)


async def test_authenticated_presence_api(store):
    runner = web.AppRunner(application(Service(store, "secret")))
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/hollywood/v2/devices/phone/presence"
    try:
        async with ClientSession() as client:
            payload = {"environment": "android", "state": "foreground"}
            async with client.post(url, json=payload) as response:
                assert response.status == 401
            client.headers["Authorization"] = "Bearer secret"
            async with client.post(url, json=payload) as response:
                assert response.status == 200
                result = await response.json()
                assert result["state"] == "foreground"
                assert result["expiresAt"] - result["receivedAt"] == 90
            for extra in (
                {"receivedAt": 9999999999},
                {"state": "online"},
                {"suppressForegroundPush": "false"},
            ):
                async with client.post(url, json={**payload, **extra}) as response:
                    assert response.status == 400
            async with client.post(url.replace("phone", "missing"), json=payload) as response:
                assert response.status == 400
            async with client.delete(url.removesuffix("/presence")) as response:
                assert response.status == 200
            async with client.post(url, json=payload) as response:
                assert response.status == 400
            with store.db:
                store.db.execute("UPDATE device SET revoked=0 WHERE id='phone'")
            assert presence.device_state(store, "phone")["state"] == "unknown"
    finally:
        await runner.cleanup()


@pytest.mark.parametrize(
    "state,preference,stale,expected",
    [
        ("foreground", True, False, "suppressed"),
        ("foreground", False, False, "sent"),
        ("background", True, False, "sent"),
        ("foreground", True, True, "sent"),
    ],
)
async def test_push_suppresses_only_foreground_device_leaving_attention_open(
    store, monkeypatch, state, preference, stale, expected
):
    presence.report(
        store,
        "phone",
        presence.PresenceReport(
            environment="android",
            state=state,
            suppressForegroundPush=preference,
        ),
        **({"now": 100} if stale else {}),
    )
    event = store.attention("lobby", "maya", "Needs your answer")
    sent = []
    monkeypatch.setattr(messaging, "send", lambda message, **kwargs: sent.append(message))
    push = object.__new__(Push)
    push.store, push.app = store, None
    worker = asyncio.create_task(push.run())
    try:
        for _ in range(300):
            if store.rows("SELECT * FROM push_delivery WHERE device='tablet' AND state='sent'"):
                break
            await asyncio.sleep(0.01)
        assert store.rows("SELECT device,state FROM push_delivery ORDER BY device") == [
            {"device": "phone", "state": expected},
            {"device": "tablet", "state": "sent"},
        ]
        assert sorted(message.fid for message in sent) == (
            ["tablet-token"] if expected == "suppressed" else ["phone-token", "tablet-token"]
        )
        assert store.rows("SELECT state FROM attention WHERE id=?", (event,)) == [{"state": "open"}]
    finally:
        worker.cancel()
        with suppress(asyncio.CancelledError):
            await worker
