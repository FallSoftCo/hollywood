"""Independent checks against real manifest parsing and durable signal state."""

import asyncio
import sqlite3

import pytest

from hollywood_room.release_signals import ReleaseSignals
from hollywood_room.store import Store
from hollywood_room.updates import Updates
from test_room_updates import fixture_release


def test_signal_restart_baseline_and_no_agent_or_attention_work(tmp_path):
    directory = tmp_path / "updates"
    fixture_release(directory, version=8)
    path = tmp_path / "room.db"
    store = Store(path)
    signals = ReleaseSignals(store, Updates(directory))
    assert signals.poll() is None
    store.close()
    store = Store(path)
    try:
        signals = ReleaseSignals(store, Updates(directory))
        assert signals.poll() is None
        release = fixture_release(directory, b"new", version=9)
        event = signals.poll()
        assert store.get_event(event)["data"]["releaseSha256"] == release.sha256
        for _ in range(5):
            assert signals.poll() is None
        assert store.rows("SELECT event FROM push_outbox") == [{"event": event}]
        assert store.rows("SELECT * FROM delivery") == []
        assert store.rows("SELECT * FROM attention") == []
        store.close()
        store = Store(path)
        assert ReleaseSignals(store, Updates(directory)).poll() is None
        assert len(store.rows("SELECT * FROM push_outbox")) == 1
    finally:
        store.close()


def test_signal_corrupt_artifact_does_not_consume_new_version(tmp_path):
    directory = tmp_path / "updates"
    fixture_release(directory, version=8)
    store = Store(tmp_path / "room.db")
    try:
        signals = ReleaseSignals(store, Updates(directory))
        signals.poll()
        release = fixture_release(directory, b"new", version=9)
        artifact = directory / f"{release.sha256}.apk"
        artifact.write_bytes(b"bad")
        with pytest.raises(ValueError):
            signals.poll()
        assert store.rows("SELECT version FROM release_signal") == [{"version": 8}]
        assert store.rows("SELECT * FROM push_outbox") == []
        artifact.write_bytes(b"new")
        assert signals.poll() is not None
        assert store.rows("SELECT version FROM release_signal") == [{"version": 9}]
    finally:
        store.close()


def test_signal_first_publication_after_empty_baseline_and_rollback(tmp_path):
    directory = tmp_path / "updates"
    store = Store(tmp_path / "room.db")
    try:
        signals = ReleaseSignals(store, Updates(directory))
        assert signals.poll() is None
        fixture_release(directory, version=9)
        event = signals.poll()
        fixture_release(directory, b"rollback", version=8)
        assert signals.poll() is None
        fixture_release(directory, version=9)
        assert signals.poll() is None
        assert store.rows("SELECT event FROM push_outbox") == [{"event": event}]
    finally:
        store.close()


def test_outbox_failure_rolls_back_release_event_and_watermark(tmp_path):
    directory = tmp_path / "updates"
    fixture_release(directory, version=8)
    store = Store(tmp_path / "room.db")
    try:
        signals = ReleaseSignals(store, Updates(directory))
        signals.poll()
        fixture_release(directory, b"new", version=9)
        with store.db:
            store.db.execute("""CREATE TRIGGER inject_outbox_failure BEFORE INSERT ON push_outbox
                BEGIN SELECT RAISE(ABORT,'injected write failure'); END""")
        with pytest.raises(sqlite3.IntegrityError):
            signals.poll()
        assert store.rows("SELECT version FROM release_signal") == [{"version": 8}]
        assert store.rows("SELECT * FROM event WHERE kind='appUpdate'") == []
        with store.db:
            store.db.execute("DROP TRIGGER inject_outbox_failure")
        assert signals.poll() is not None
        assert len(store.rows("SELECT * FROM push_outbox")) == 1
    finally:
        store.close()


async def test_push_worker_discovers_real_publication_and_emits_hint(tmp_path, monkeypatch):
    from firebase_admin import messaging
    from hollywood_room.push import Push

    directory = tmp_path / "updates"
    fixture_release(directory, version=8)
    store = Store(tmp_path / "room.db")
    signals = ReleaseSignals(store, Updates(directory))
    signals.poll()
    with store.db:
        store.db.execute("INSERT INTO device(id,token) VALUES('review','fixture-fid')")
    release = fixture_release(directory, b"new", version=9)
    sent = []
    monkeypatch.setattr(messaging, "send", lambda message, **kwargs: sent.append(message))
    push = object.__new__(Push)
    push.store, push.app, push.release_signals = store, None, signals
    worker = asyncio.create_task(push.run())
    try:
        for _ in range(100):
            if sent:
                break
            await asyncio.sleep(0.01)
        assert len(sent) == 1
        assert sent[0].data["kind"] == "appUpdate"
        assert sent[0].data["releaseSha256"] == release.sha256
        assert sent[0].data["versionCode"] == "9"
        assert store.rows("SELECT * FROM attention") == []
        assert store.rows("SELECT * FROM delivery") == []
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        store.close()
