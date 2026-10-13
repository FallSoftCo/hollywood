import asyncio
from types import SimpleNamespace

from firebase_admin import messaging

from hollywood_room.push import Push
from hollywood_room.release_signals import ReleaseSignals
from hollywood_room.store import Store


def test_publication_once_across_restart_without_startup_or_downgrade_spam(tmp_path):
    path = tmp_path / "room.db"
    store = Store(path)
    release = {"available": True, "versionCode": 17, "versionName": "0.4.9", "sha256": "a" * 64}
    updates = SimpleNamespace(manifest=lambda: release)
    signals = ReleaseSignals(store, updates)
    assert signals.poll() is None
    release.update(versionCode=18, versionName="0.5.0", sha256="b" * 64)
    event = signals.poll()
    assert event is not None
    assert signals.poll() is None
    assert store.rows("SELECT * FROM attention") == []
    assert store.rows("SELECT * FROM delivery") == []
    store.close()
    store = Store(path)
    try:
        signals = ReleaseSignals(store, updates)
        assert signals.poll() is None
        release.update(versionCode=17, sha256="a" * 64)
        assert signals.poll() is None
        assert store.rows("SELECT event FROM push_outbox") == [{"event": event}]
    finally:
        store.close()


async def test_first_publication_after_empty_baseline_sends_only_manifest_hint(
    tmp_path, monkeypatch
):
    store = Store(tmp_path / "room.db")
    release = {"available": False}
    signals = ReleaseSignals(store, SimpleNamespace(manifest=lambda: release))
    assert signals.poll() is None
    with store.db:
        store.db.execute("INSERT INTO device(id,token) VALUES('fixture','fixture-fid')")
    release.update(available=True, versionCode=18, versionName="0.5.0", sha256="c" * 64)
    event = signals.poll()
    sent = []
    monkeypatch.setattr(messaging, "send", lambda message, **kwargs: sent.append(message))
    push = object.__new__(Push)
    push.store, push.app = store, None
    worker = asyncio.create_task(push.run())
    try:
        for _ in range(100):
            if store.rows("SELECT * FROM push_delivery WHERE state='sent'"):
                break
            await asyncio.sleep(0.01)
        assert len(sent) == 1
        data = sent[0].data
        assert data["attentionId"] == str(event)
        assert data["deviceId"] == "fixture"
        assert data["kind"] == "appUpdate"
        assert data["releaseSha256"] == "c" * 64
        assert data["versionCode"] == "18"
        assert data["versionName"] == "0.5.0"
        assert not ({"apkPath", "url", "token", "command"} & data.keys())
        assert sent[0].notification is None
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        store.close()
