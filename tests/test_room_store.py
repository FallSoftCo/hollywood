import pytest

from hollywood_room.models import Message
from hollywood_room.server import Service
from hollywood_room.store import Conflict, Store


@pytest.fixture
def store(tmp_path):
    value = Store(tmp_path / "room.db")
    yield value
    value.close()


def test_retry_keeps_one_message_and_delivery(store):
    service = Service(store, "test-token")
    message = Message(commandId="reconnect", body="Maya, inspect notification privacy")
    first = service.post(message)
    assert service.post(message) == first
    assert store.rows("SELECT event,agent,authority,state FROM delivery") == [
        {"event": first["id"], "agent": "maya", "authority": "user", "state": "queued"}
    ]
    with pytest.raises(Conflict):
        service.post(message.model_copy(update={"body": "different action"}))


def test_private_history_cannot_be_read_by_another_agent(store):
    service = Service(store, "test-token")
    public = service.post(Message(commandId="room", body="Project requirement"))
    private = service.post(
        Message(commandId="private", body="Private analysis", targets=["maya"], visibility="direct")
    )
    assert store.events(visibility="direct:theo") == [public]
    assert store.events(visibility="direct:maya") == [public, private]
    reply = service.post(
        Message(commandId="reply", body="Continue privately", replyTo=private["id"])
    )
    assert (reply["visibility"], reply["data"]["targets"]) == ("direct:maya", ["maya"])
    with pytest.raises(ValueError, match="cannot change recipients"):
        service.post(
            Message(commandId="leak", body="Forward", replyTo=private["id"], targets=["theo"])
        )


def test_reply_keeps_original_task_and_unknown_mentions_do_not_dispatch(store):
    service = Service(store, "test-token")
    store.add_task("android", "Android notifications", None)
    original = service.post(Message(commandId="a", body="Theo, inspect delivery", task="android"))
    reply = service.post(
        Message(commandId="b", body="Use the generic alert", replyTo=original["id"])
    )
    assert (reply["task"], reply["data"]["targets"]) == ("android", ["theo"])
    with pytest.raises(ValueError, match="Unknown recipient"):
        service.post(Message(commandId="bad", body="@missing fix this"))
    assert len(store.rows("SELECT * FROM delivery")) == 2


def test_restart_keeps_queued_work_and_does_not_replay_uncertain_effects(tmp_path):
    path = tmp_path / "room.db"
    store = Store(path)
    service = Service(store, "test-token")
    event = service.post(Message(commandId="pending", body="Maya, inspect this"))
    with store.db:
        store.db.execute("UPDATE delivery SET state='dispatching'")
    store.close()
    reopened = Store(path)
    try:
        assert reopened.events() == [event]
        assert reopened.rows("SELECT state FROM delivery") == [{"state": "uncertain"}]
    finally:
        reopened.close()


def test_only_one_service_can_own_a_database(tmp_path):
    from filelock import Timeout

    first = Store(tmp_path / "room.db")
    try:
        with pytest.raises(Timeout):
            Store(tmp_path / "room.db")
    finally:
        first.close()


def test_attention_and_notification_are_committed_together(store):
    event = store.attention("lobby", "theo", "Which privacy policy?")
    assert store.rows("SELECT event,state FROM push_outbox") == [
        {"event": event, "state": "pending"}
    ]
    assert store.snapshot()["attention"] == [
        {"id": event, "task": "lobby", "agent": "theo", "state": "open", "response": None}
    ]
