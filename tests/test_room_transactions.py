import sqlite3

import pytest

from hollywood_room.store import Store


def test_delegation_commits_task_authorization_and_brief_as_one_unit(tmp_path):
    store = Store(tmp_path / "room.db")
    try:
        source = store.message(
            "human",
            task="lobby",
            author="you",
            body="Delegate this task",
            targets=["coordinator"],
            authority="user",
        )
        baseline = store.snapshot()
        store.db.execute("""CREATE TRIGGER fail_brief BEFORE INSERT ON delivery
            WHEN NEW.authority='tool' BEGIN SELECT RAISE(ABORT,'injected storage failure'); END""")
        with pytest.raises(sqlite3.IntegrityError, match="injected storage failure"):
            store.message(
                "assignment",
                task="android",
                author="coordinator",
                body="Review it",
                targets=["maya"],
                authority="tool",
                assignment={"source": source["id"], "title": "Android"},
            )
        assert store.snapshot() == baseline
        assert store.events() == [source]
        store.db.execute("DROP TRIGGER fail_brief")
        result = store.message(
            "assignment",
            task="android",
            author="coordinator",
            body="Review it",
            targets=["maya"],
            authority="tool",
            assignment={"source": source["id"], "title": "Android"},
        )
        assert store.rows(
            "SELECT event,authority FROM delivery WHERE task='android' ORDER BY id"
        ) == [
            {"event": source["id"], "authority": "userReference"},
            {"event": result["id"], "authority": "tool"},
        ]
    finally:
        store.close()


def test_private_source_cannot_be_forwarded_as_delegated_authority(tmp_path):
    store = Store(tmp_path / "room.db")
    try:
        private = store.message(
            "private",
            task="lobby",
            author="you",
            body="Private diagnostic",
            targets=["maya"],
            authority="user",
            visibility="direct:maya",
        )
        baseline = store.snapshot()
        with pytest.raises(ValueError, match="public human task request"):
            store.message(
                "assignment",
                task="review",
                author="maya",
                body="Ask Theo",
                targets=["theo"],
                authority="tool",
                assignment={"source": private["id"], "title": "Review"},
            )
        assert store.snapshot() == baseline
        assert store.events(visibility="direct:theo") == []
    finally:
        store.close()
