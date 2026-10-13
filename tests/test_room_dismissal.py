import sqlite3

import pytest
from aiohttp import ClientSession, web

from hollywood_room.server import Service, application
from hollywood_room.store import Store


@pytest.fixture
async def endpoint(tmp_path):
    store = Store(tmp_path / "room.db")
    runner = web.AppRunner(application(Service(store, "test-token")))
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    async with ClientSession() as client:
        yield (
            store,
            client,
            f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/hollywood/v2",
        )
    await runner.cleanup()
    store.close()


async def test_dismiss_auth_scope_idempotency_totals_and_history(endpoint):
    store, client, base = endpoint
    store.add_team("other", "Other", "lead-o", "Lead")
    question = store.attention(
        "team-other", "lead-o", "Private stale question", visibility="direct:lead-o"
    )
    original = store.get_event(question)
    payload = {"commandId": "dismiss-one"}
    path = f"/teams/other/attention/{question}/dismiss"
    response = await client.post(base + path, json=payload)
    assert response.status == 401
    client.headers["Authorization"] = "Bearer test-token"
    response = await client.post(base + f"/attention/{question}/dismiss", json=payload)
    assert response.status == 400
    baseline = store.snapshot()
    for _ in range(2):
        response = await client.post(base + path, json=payload)
        assert response.status == 200
        assert await response.json() == {"id": question, "state": "dismissed", "dismissed": True}
    assert store.get_event(question) == original
    assert store.rows("SELECT * FROM delivery") == []
    assert store.snapshot()["cursor"] == baseline["cursor"] + 1
    response = await client.get(base + "/attention")
    assert (await response.json())["total_pending"] == 0
    response = await client.get(base + f"/teams/other/messages/{question}")
    assert (await response.json())["attentionState"] == "dismissed"
    response = await client.get(base + "/teams/other/history")
    assert (await response.json())["data"] == []
    response = await client.get(base + "/teams/other/history?visibility=direct:lead-o")
    assert {event["kind"] for event in (await response.json())["data"]} == {
        "attention",
        "dismissed",
    }
    response = await client.post(
        base + f"/teams/other/attention/{question}/answer",
        json={"commandId": "late-answer", "body": "No"},
    )
    assert response.status == 409


async def test_chat_reply_resolves_question_and_stale_dismiss_returns_resolved(endpoint):
    store, client, base = endpoint
    client.headers["Authorization"] = "Bearer test-token"
    question = store.attention("lobby", "maya", "Question", visibility="direct:maya")
    response = await client.post(
        base + "/messages", json={"commandId": "chat-answer", "body": "Answer", "replyTo": question}
    )
    assert response.status == 202
    assert (await response.json())["visibility"] == "direct:maya"
    assert store.rows("SELECT state,response FROM attention WHERE id=?", (question,)) == [
        {"state": "resolved", "response": "Answer"}
    ]
    before = store.snapshot()["cursor"]
    for _ in range(2):
        response = await client.post(
            base + f"/attention/{question}/dismiss", json={"commandId": "stale-dismiss"}
        )
        assert response.status == 200
        assert (await response.json())["state"] == "resolved"
    assert store.snapshot()["cursor"] == before + 1
    response = await client.get(base + "/attention")
    assert (await response.json())["total_pending"] == 0


def test_dismiss_transaction_failure_and_restart(tmp_path):
    path = tmp_path / "room.db"
    store = Store(path)
    question = store.attention("lobby", "maya", "Question")
    with store.db:
        store.db.execute(
            "CREATE TRIGGER reject_dismiss BEFORE INSERT ON command BEGIN SELECT RAISE(ABORT,'injected'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        store.dismiss_attention("dismiss", question, "default")
    assert store.rows("SELECT state FROM attention WHERE id=?", (question,))[0]["state"] == "open"
    assert store.snapshot()["cursor"] == question
    store.db.execute("DROP TRIGGER reject_dismiss")
    result = store.dismiss_attention("dismiss", question, "default")
    store.close()
    store = Store(path)
    assert store.dismiss_attention("dismiss", question, "default") == result
    assert store.snapshot()["attention"] == []
    store.close()


async def test_open_approval_requires_explicit_review_and_command_reuse_conflicts(endpoint):
    store, client, base = endpoint
    client.headers["Authorization"] = "Bearer test-token"
    approval = store.attention("lobby", "maya", "Approval")
    other = store.attention("lobby", "maya", "Question")
    with store.db:
        store.db.execute("UPDATE event SET kind='approval' WHERE id=?", (approval,))
    response = await client.post(base + f"/attention/{approval}/dismiss", json={"commandId": "a"})
    assert response.status == 409
    response = await client.post(base + f"/attention/{other}/dismiss", json={"commandId": "a"})
    assert response.status == 200
    response = await client.post(base + f"/attention/{approval}/dismiss", json={"commandId": "a"})
    assert response.status == 409
    with store.db:
        store.db.execute("UPDATE attention SET state='expired' WHERE id=?", (approval,))
    response = await client.post(
        base + f"/attention/{approval}/dismiss", json={"commandId": "expired"}
    )
    assert await response.json() == {"id": approval, "state": "expired", "dismissed": True}


async def test_resolved_question_and_answer_cards_keep_dismissal_on_reload(endpoint):
    store, client, base = endpoint
    client.headers["Authorization"] = "Bearer test-token"
    question = store.attention("lobby", "maya", "Question", visibility="direct:maya")
    response = await client.post(
        base + f"/attention/{question}/answer",
        json={"commandId": "answered", "body": "Keep this answer"},
    )
    answer = await response.json()
    with store.db:
        store.db.execute("UPDATE task SET status='completed' WHERE id='lobby'")
    original_question, original_answer = store.get_event(question), store.get_event(answer["id"])
    for command in ("dismiss-answer", "dismiss-answer", "dismiss-again"):
        response = await client.post(
            base + f"/attention/{question}/dismiss", json={"commandId": command}
        )
        assert response.status == 200
        assert await response.json() == {"id": question, "state": "resolved", "dismissed": True}
    assert store.get_event(question) == original_question
    assert store.get_event(answer["id"]) == original_answer
    assert store.rows("SELECT response,state FROM attention WHERE id=?", (question,)) == [
        {"response": "Keep this answer", "state": "resolved"}
    ]
    assert store.rows("SELECT status FROM task WHERE id='lobby'")[0]["status"] == "completed"
    assert len(store.rows("SELECT * FROM attention_dismissal WHERE id=?", (question,))) == 1
    assert len(store.rows("SELECT * FROM event WHERE kind='dismissed'")) == 1
    resolved = store.rows("SELECT id FROM event WHERE kind='resolved'")[0]["id"]
    for event_id in (question, answer["id"], resolved):
        response = await client.get(base + f"/messages/{event_id}")
        detail = await response.json()
        assert detail["dismissed"] is True
        assert detail["attentionId"] == question
        assert detail["attentionState"] == "resolved"
    response = await client.get(base + "/history?visibility=direct:maya")
    cards = {event["id"]: event for event in (await response.json())["data"]}
    assert cards[question]["dismissed"] is True
    assert cards[answer["id"]]["dismissed"] is True
    assert cards[resolved]["dismissed"] is True
    response = await client.get(base + "/history")
    assert (await response.json())["data"] == []
    response = await client.get(base + "/attention")
    assert (await response.json())["total_pending"] == 0


def test_dismissed_card_marker_survives_restart_and_legacy_state_migrates(tmp_path):
    path = tmp_path / "room.db"
    store = Store(path)
    question = store.attention("lobby", "maya", "Question")
    store.dismiss_attention("dismiss", question, "default")
    store.db.execute("DROP TABLE attention_dismissal")
    store.db.commit()
    store.close()
    store = Store(path)
    assert store.attention_card_state(store.get_event(question))["dismissed"] is True
    assert store.dismiss_attention("dismiss", question, "default") == {
        "id": question,
        "state": "dismissed",
        "dismissed": True,
    }
    store.close()
