import pytest
from aiohttp import ClientSession, web

from hollywood_room.server import Service, application
from hollywood_room.store import Store


@pytest.fixture
async def service(tmp_path):
    store = Store(tmp_path / "room.db")
    service = Service(store, "fixture-secret")
    runner = web.AppRunner(application(service))
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    yield service, url
    await runner.cleanup()
    store.close()


async def test_auth_actor_binding_replay_and_idempotent_send(service):
    service, url = service
    async with ClientSession() as client:
        async with client.get(url + "/hollywood/v2/overview") as response:
            assert response.status == 401
        client.headers["Authorization"] = "Bearer fixture-secret"
        payload = {"commandId": "one", "body": "Maya, inspect privacy"}
        async with client.post(url + "/hollywood/v2/messages", json=payload) as response:
            event = await response.json()
            assert response.status == 202
        async with client.post(url + "/hollywood/v2/messages", json=payload) as response:
            assert await response.json() == event
        async with client.post(
            url + "/hollywood/v2/messages", json={**payload, "author": "maya"}
        ) as response:
            assert response.status == 400
        async with client.get(url + "/hollywood/v2/events?after=0") as response:
            assert await response.content.readline() == f"id: {event['id']}\n".encode()
        # A fresh observer reconstructs the same history without changing dispatch state.
        async with client.get(url + "/hollywood/v2/messages?after=0") as response:
            assert (await response.json())["data"] == [event]
        assert service.store.rows("SELECT agent,state FROM delivery") == [
            {"agent": "maya", "state": "queued"}
        ]


async def test_answer_is_bound_to_attention_and_stale_actions_fail(service):
    service, url = service
    attention = service.store.attention("lobby", "theo", "Generic or task-specific alert?")
    async with ClientSession(headers={"Authorization": "Bearer fixture-secret"}) as client:
        path = url + f"/hollywood/v2/attention/{attention}/answer"
        payload = {"commandId": "answer", "body": "Generic"}
        async with client.post(path, json=payload) as response:
            event = await response.json()
            assert response.status == 200
        async with client.post(path, json=payload) as response:
            assert await response.json() == event
        async with client.post(path, json={**payload, "commandId": "stale"}) as response:
            assert response.status == 409
        assert service.store.snapshot()["attention"] == []


async def test_private_question_answer_stays_private_and_cannot_reuse_another_receipt(service):
    service, url = service
    private = service.store.attention("lobby", "maya", "Private question", visibility="direct:maya")
    other = service.store.attention("lobby", "theo", "Separate question")
    async with ClientSession(headers={"Authorization": "Bearer fixture-secret"}) as client:
        for attention, command in ((private, "private-answer"), (other, "other-answer")):
            async with client.post(
                url + f"/hollywood/v2/attention/{attention}/answer",
                json={"commandId": command, "body": "Yes"},
            ) as response:
                assert response.status == 200
        async with client.post(
            url + f"/hollywood/v2/attention/{private}/answer",
            json={"commandId": "other-answer", "body": "Yes"},
        ) as response:
            assert response.status == 409
        visible = service.store.events(visibility="direct:theo")
        assert all(e["visibility"] == "room" for e in visible)
        assert all(e["data"].get("replyTo") != private for e in visible)
