import pytest
from aiohttp import ClientSession, web

from hollywood_room.models import Message
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


async def test_history_reads_every_page_across_live_inserts_without_crossing_conversations(service):
    service, url = service
    expected = [service.store.add_task("client", "Client", None)]
    with service.store.db:
        for index in range(650):
            task = "lobby" if index % 2 else "client"
            expected.append(service.store.event("message", task, "maya", f"Message {index}"))
            service.store.event("message", task, "theo", "Private", visibility="direct:theo")
            expected.append(service.store.event("artifact", task, "maya", "Tool output"))
    async with ClientSession(headers={"Authorization": "Bearer fixture-secret"}) as client:

        async def page(**params):
            async with client.get(url + "/hollywood/v2/history", params=params) as response:
                assert response.status == 200
                return await response.json()

        current = await page(limit=37)
        assert current["newerCursor"] is None
        assert [e["id"] for e in current["data"]] == expected[-37:][::-1]
        with service.store.db:
            live = service.store.event("message", "lobby", "rowan", "New while reading")
        found = [e["id"] for e in current["data"]]
        while current["olderCursor"] is not None:
            current = await page(before=current["olderCursor"], limit=37)
            found.extend(e["id"] for e in current["data"])
        assert found == expected[::-1]
        newer = await page(after=expected[-1])
        assert [e["id"] for e in newer["data"]] == [live]
        assert newer["newerCursor"] is None
        anchor = expected[311]
        around = await page(around=anchor, limit=20)
        assert [e["id"] for e in around["data"]] == expected[302:322][::-1]
        assert around["olderCursor"] == expected[302]
        assert around["newerCursor"] == expected[321]
        private = await page(visibility="direct:theo", task="client", limit=100)
        assert len(private["data"]) == 100
        assert {(e["task"], e["visibility"]) for e in private["data"]} == {
            ("client", "direct:theo")
        }
        assert service.store.snapshot()["deliveries"] == []


async def test_history_quotes_and_attention_reflect_durable_state(service):
    service, url = service
    question = service.store.attention(
        "lobby", "theo", "Choose a delivery style", visibility="direct:theo"
    )
    result = service.post(
        Message(
            commandId="history-reply",
            body="Generic alerts",
            targets=["theo"],
            replyTo=question,
            visibility="direct",
        ),
        resolves=question,
    )
    async with ClientSession(headers={"Authorization": "Bearer fixture-secret"}) as client:
        async with client.get(
            url + "/hollywood/v2/history", params={"visibility": "direct:theo"}
        ) as response:
            page = await response.json()
        question_event = next(e for e in page["data"] if e["id"] == question)
        answer = next(e for e in page["data"] if e["id"] == result["id"])
        assert question_event["open"] is False
        assert answer["reply"] == {
            "id": question,
            "author": "theo",
            "body": "Choose a delivery style",
        }
        async with client.get(url + "/hollywood/v2/history") as response:
            assert await response.json() == {"data": [], "olderCursor": None, "newerCursor": None}


@pytest.mark.parametrize(
    "params",
    [
        {"before": "0"},
        {"limit": "101"},
        {"before": "3", "after": "5"},
        {"visibility": "direct:unknown"},
        {"task": "../private"},
        {"offset": "100"},
    ],
)
async def test_history_rejects_invalid_cursors_and_scopes(service, params):
    _, url = service
    async with ClientSession() as client:
        async with client.get(url + "/hollywood/v2/history") as response:
            assert response.status == 401
        async with client.get(
            url + "/hollywood/v2/history",
            params=params,
            headers={"Authorization": "Bearer fixture-secret"},
        ) as response:
            assert response.status == 400
