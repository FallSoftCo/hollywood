import asyncio
from contextlib import suppress

from firebase_admin import messaging

from hollywood_room.push import Push
from hollywood_room.store import Store


async def test_push_omits_history_and_resolved_questions_and_includes_context(
    tmp_path, monkeypatch
):
    store = Store(tmp_path / "room.db")
    old = store.attention("lobby", "maya", "Historical question")
    with store.db:
        store.db.execute(
            "INSERT INTO device(id,token,after_event) VALUES(?,?,?)", ("device", "fixture-fid", old)
        )
    resolved = store.attention("lobby", "theo", "Already answered private question")
    pending = store.attention(
        "lobby",
        "rowan",
        "Can you review the Android notification layout?",
        visibility="direct:rowan",
    )
    with store.db:
        store.db.execute("UPDATE attention SET state='resolved' WHERE id=?", (resolved,))
    sent = []

    def send(message, *, app):
        sent.append(message)
        return "fixture-provider-acknowledgment"

    monkeypatch.setattr(messaging, "send", send)
    push = object.__new__(Push)
    push.store, push.app = store, None
    worker = asyncio.create_task(push.run())
    try:
        for _ in range(300):
            if store.rows("SELECT * FROM push_delivery WHERE state='sent'"):
                break
            await asyncio.sleep(0.01)
        assert store.rows("SELECT event,state FROM push_delivery ORDER BY event") == [
            {"event": resolved, "state": "expired"},
            {"event": pending, "state": "sent"},
        ]
        assert len(sent) == 1
        assert sent[0].data == {
            "attentionId": str(pending),
            "deviceId": "device",
            "author": "rowan",
            "task": "lobby",
            "team": "default",
            "team_id": "default",
            "taskTitle": "Project room",
            "preview": "Can you review the Android notification layout?",
            "kind": "attention",
            "visibility": "direct:rowan",
        }
        assert sent[0].fid == "fixture-fid"
        assert sent[0].android.priority == "high"
        assert sent[0].notification is None
    finally:
        worker.cancel()
        with suppress(asyncio.CancelledError):
            await worker
        store.close()
