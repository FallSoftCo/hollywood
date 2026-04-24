import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import hollywood


class HollywoodTests(unittest.TestCase):
    def test_slugify_room_segment_normalizes_text(self) -> None:
        self.assertEqual(
            hollywood.slugify_room_segment(" Losangelex / Runtime "),
            "losangelex-runtime",
        )

    def test_typed_room_name_helpers_build_expected_paths(self) -> None:
        self.assertEqual(
            hollywood.repo_room_name("Losangelex"),
            "repo/losangelex",
        )
        self.assertEqual(
            hollywood.task_room_name("Losangelex", "Ack Loop Fix"),
            "task/losangelex/ack-loop-fix",
        )
        self.assertEqual(
            hollywood.multi_room_name("Coordination Architecture"),
            "multi/coordination-architecture",
        )

    def test_describe_room_classifies_typed_room_names(self) -> None:
        self.assertEqual(
            hollywood.describe_room("main"),
            {"kind": "main", "room": "main", "repo": None, "task": None},
        )
        self.assertEqual(
            hollywood.describe_room("repo/losangelex"),
            {"kind": "repo", "room": "repo/losangelex", "repo": "losangelex", "task": None},
        )
        self.assertEqual(
            hollywood.describe_room("task/losangelex/ack-loop-fix"),
            {
                "kind": "task",
                "room": "task/losangelex/ack-loop-fix",
                "repo": "losangelex",
                "task": "ack-loop-fix",
            },
        )
        self.assertEqual(
            hollywood.describe_room("ad-hoc-room"),
            {"kind": "custom", "room": "ad-hoc-room", "repo": None, "task": None},
        )

    def test_alias_roundtrip(self) -> None:
        session_id = "019d0cee-31b5-7133-843c-10d1c562e157"
        alias = hollywood.session_id_to_alias(session_id)

        self.assertEqual(
            hollywood.alias_to_session_id(alias),
            session_id,
        )

    def test_normalize_id_accepts_alias(self) -> None:
        session_id = "019d0cee-31b5-7133-843c-10d1c562e157"
        alias = hollywood.session_id_to_alias(session_id)

        self.assertEqual(hollywood.normalize_id(alias), session_id)

    def test_cursor_path_replaces_slashes(self) -> None:
        path = hollywood.cursor_path("~/.hollywood/cursors", "main/room", "agent/1")

        self.assertEqual(path.name, "main_room.agent_1.cursor")

    def test_init_db_adds_message_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "hollywood.db")
            hollywood.init_db(db_path)
            row = hollywood.insert_message(
                db_path,
                room="main",
                sender_id="sender",
                recipient_id=None,
                message_kind=hollywood.MESSAGE_KIND_BROADCAST,
                response_policy=hollywood.RESPONSE_POLICY_NONE,
                body="hello room",
            )
            self.assertEqual(row["message_kind"], hollywood.MESSAGE_KIND_BROADCAST)
            self.assertEqual(row["response_policy"], hollywood.RESPONSE_POLICY_NONE)

    def test_init_db_sets_schema_version_and_backfills_room_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "hollywood.db")
            conn = sqlite3.connect(db_path)
            try:
                conn.execute(
                    """
                    CREATE TABLE messages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        room TEXT NOT NULL,
                        sender_id TEXT NOT NULL,
                        recipient_id TEXT,
                        body TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO messages(room, sender_id, recipient_id, body, created_at)
                    VALUES ('repo/losangelex', 'sender', NULL, 'hello', '2026-04-23T00:00:00Z')
                    """
                )
                conn.commit()
            finally:
                conn.close()

            hollywood.init_db(db_path)

            conn = sqlite3.connect(db_path)
            try:
                self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], hollywood.SCHEMA_VERSION)
            finally:
                conn.close()

            room_state = hollywood.get_room_state(db_path, "repo/losangelex")
            self.assertIsNotNone(room_state)
            assert room_state is not None
            self.assertEqual(room_state["kind"], "repo")
            self.assertEqual(room_state["repo"], "losangelex")
            self.assertEqual(room_state["state_version"], 1)
            self.assertEqual(room_state["contract_version"], hollywood.ROOM_CONTRACT_VERSION)
            self.assertEqual(room_state["last_message_id"], 1)

    def test_insert_message_creates_room_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "hollywood.db")
            hollywood.init_db(db_path)
            inserted = hollywood.insert_message(
                db_path,
                room="task/losangelex/ack-loop-fix",
                sender_id="sender",
                recipient_id=None,
                message_kind=hollywood.MESSAGE_KIND_AMBIENT,
                response_policy=hollywood.RESPONSE_POLICY_OPTIONAL,
                body="hello room",
            )

            room_state = hollywood.get_room_state(db_path, "task/losangelex/ack-loop-fix")
            self.assertIsNotNone(room_state)
            assert room_state is not None
            self.assertEqual(room_state["kind"], "task")
            self.assertEqual(room_state["repo"], "losangelex")
            self.assertEqual(room_state["task"], "ack-loop-fix")
            self.assertEqual(room_state["last_message_id"], inserted["id"])

    def test_upsert_room_state_can_bump_state_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "hollywood.db")
            hollywood.init_db(db_path)
            hollywood.insert_message(
                db_path,
                room="repo/losangelex",
                sender_id="sender",
                recipient_id=None,
                message_kind=hollywood.MESSAGE_KIND_AMBIENT,
                response_policy=hollywood.RESPONSE_POLICY_OPTIONAL,
                body="hello room",
            )

            updated = hollywood.upsert_room_state(
                db_path,
                {
                    "room": "repo/losangelex",
                    "bump_state_version": True,
                },
            )
            self.assertEqual(updated["state_version"], 2)
            self.assertEqual(updated["contract_version"], hollywood.ROOM_CONTRACT_VERSION)

            listed = hollywood.list_rooms(db_path, "repo/losangelex", 10)
            self.assertEqual(len(listed), 1)
            self.assertEqual(listed[0]["state_version"], 2)

    def test_format_line_includes_message_kind_and_response_policy(self) -> None:
        formatted = hollywood.format_line(
            {
                "id": 9,
                "created_at": "2026-03-21T00:00:00Z",
                "sender_id": "sender",
                "recipient_id": None,
                "message_kind": hollywood.MESSAGE_KIND_BROADCAST,
                "response_policy": hollywood.RESPONSE_POLICY_NONE,
                "body": "hello",
            }
        )

        self.assertIn("[broadcast reply=none]", formatted)
        self.assertIn("reply=none", formatted)

    def test_registry_upsert_and_list_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "hollywood.db")
            hollywood.init_db(db_path)
            entry = hollywood.upsert_registry_entry(
                db_path,
                {
                    "session_id": "019d0cee-31b5-7133-843c-10d1c562e157",
                    "room": "main",
                    "attached": True,
                    "cwd": "/home/ai/Development/micro-browser",
                    "repo_name": "micro-browser",
                    "attention_mode": "focused",
                    "identities": [
                        "019d0cee-31b5-7133-843c-10d1c562e157",
                        "sid-aaaa-bbbb-cccc-dddd-eeee-ffff-gg",
                    ],
                    "session_kind": "resumed",
                    "status": "idle",
                    "role": "owner",
                    "task": "Bowser support",
                    "scope": "pdf handling",
                },
            )
            self.assertEqual(entry["repo_name"], "micro-browser")
            self.assertEqual(entry["status"], "idle")
            self.assertEqual(len(entry["identities"]), 2)

            listed = hollywood.list_registry_entries(db_path, "main", 10)
            self.assertEqual(len(listed), 1)
            self.assertEqual(listed[0]["session_id"], entry["session_id"])
            self.assertEqual(listed[0]["scope"], "pdf handling")

    def test_registry_upsert_preserves_optional_semantic_fields_when_omitted(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "hollywood.db")
            hollywood.init_db(db_path)
            session_id = "019d0cee-31b5-7133-843c-10d1c562e157"
            hollywood.upsert_registry_entry(
                db_path,
                {
                    "session_id": session_id,
                    "room": "main",
                    "status": "idle",
                    "role": "owner",
                    "task": "Initial task",
                    "scope": "Initial scope",
                },
            )
            entry = hollywood.upsert_registry_entry(
                db_path,
                {
                    "session_id": session_id,
                    "room": "main",
                    "status": "active",
                },
            )
            self.assertEqual(entry["status"], "active")
            self.assertEqual(entry["role"], "owner")
            self.assertEqual(entry["task"], "Initial task")
            self.assertEqual(entry["scope"], "Initial scope")

    def test_registry_rejects_duplicate_attached_logical_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "hollywood.db")
            hollywood.init_db(db_path)
            hollywood.upsert_registry_entry(
                db_path,
                {
                    "session_id": "019d0cee-31b5-7133-843c-10d1c562e157",
                    "room": "main",
                    "attached": True,
                    "identities": [
                        "019d0cee-31b5-7133-843c-10d1c562e157",
                        "scout",
                    ],
                    "status": "idle",
                },
            )

            with self.assertRaisesRegex(ValueError, "logical identity already attached"):
                hollywood.upsert_registry_entry(
                    db_path,
                    {
                        "session_id": "019d0cee-2a81-77f1-902e-e3cff7984c2f",
                        "room": "main",
                        "attached": True,
                        "identities": [
                            "019d0cee-2a81-77f1-902e-e3cff7984c2f",
                            "scout",
                        ],
                        "status": "idle",
                    },
                )

    def test_registry_allows_reusing_logical_identity_after_detach(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "hollywood.db")
            hollywood.init_db(db_path)
            first_session = "019d0cee-31b5-7133-843c-10d1c562e157"
            second_session = "019d0cee-2a81-77f1-902e-e3cff7984c2f"
            hollywood.upsert_registry_entry(
                db_path,
                {
                    "session_id": first_session,
                    "room": "main",
                    "attached": True,
                    "identities": [first_session, "scout"],
                    "status": "idle",
                },
            )
            hollywood.upsert_registry_entry(
                db_path,
                {
                    "session_id": first_session,
                    "room": "main",
                    "attached": False,
                    "identities": [first_session, "scout"],
                    "status": "done",
                },
            )
            entry = hollywood.upsert_registry_entry(
                db_path,
                {
                    "session_id": second_session,
                    "room": "main",
                    "attached": True,
                    "identities": [second_session, "scout"],
                    "status": "idle",
                },
            )
            self.assertEqual(entry["session_id"], second_session)

    def test_registry_allows_reusing_logical_identity_after_stale_heartbeat(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "hollywood.db")
            hollywood.init_db(db_path)
            first_session = "019d0cee-31b5-7133-843c-10d1c562e157"
            second_session = "019d0cee-2a81-77f1-902e-e3cff7984c2f"
            hollywood.upsert_registry_entry(
                db_path,
                {
                    "session_id": first_session,
                    "room": "main",
                    "attached": True,
                    "identities": [first_session, "scout"],
                    "status": "idle",
                },
            )

            stale_at = (
                datetime.now(timezone.utc) - hollywood.REGISTRY_STALE_AFTER - timedelta(seconds=5)
            ).replace(microsecond=0)
            stale_iso = stale_at.isoformat().replace("+00:00", "Z")
            conn = sqlite3.connect(db_path)
            try:
                conn.execute(
                    "UPDATE registry SET updated_at = ?, last_heartbeat_at = ? WHERE session_id = ?",
                    (stale_iso, stale_iso, first_session),
                )
                conn.commit()
            finally:
                conn.close()

            entry = hollywood.upsert_registry_entry(
                db_path,
                {
                    "session_id": second_session,
                    "room": "repo/losangelex",
                    "attached": True,
                    "identities": [second_session, "scout"],
                    "status": "idle",
                },
            )
            self.assertEqual(entry["session_id"], second_session)

    def test_team_create_and_list_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "hollywood.db")
            hollywood.init_db(db_path)
            leader = "019d0cee-31b5-7133-843c-10d1c562e157"
            member = "019d0cee-2a81-77f1-902e-e3cff7984c2f"
            team = hollywood.create_team(
                db_path,
                {
                    "room": "main",
                    "task_room": "task-bowser",
                    "purpose": "coordinate bowser work",
                    "leader_session_id": leader,
                    "members": [
                        {"session_id": member, "role": "member", "state": "pending"},
                    ],
                },
            )
            self.assertEqual(team["room"], "main")
            self.assertEqual(team["task_room"], "task-bowser")
            self.assertEqual(team["leader_session_id"], leader)
            self.assertEqual(len(team["members"]), 2)

            listed = hollywood.list_teams(db_path, "main", 10)
            self.assertEqual(len(listed), 1)
            self.assertEqual(listed[0]["team_id"], team["team_id"])
            self.assertEqual(listed[0]["purpose"], "coordinate bowser work")

    def test_team_member_update_tracks_state_and_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "hollywood.db")
            hollywood.init_db(db_path)
            leader = "019d0cee-31b5-7133-843c-10d1c562e157"
            member = "019d0cee-2a81-77f1-902e-e3cff7984c2f"
            team = hollywood.create_team(
                db_path,
                {
                    "room": "main",
                    "task_room": "task-bowser",
                    "purpose": "coordinate bowser work",
                    "leader_session_id": leader,
                    "members": [
                        {"session_id": member, "role": "member", "state": "pending"},
                    ],
                },
            )

            updated = hollywood.upsert_team_member(
                db_path,
                {
                    "team_id": team["team_id"],
                    "session_id": member,
                    "state": "active",
                    "joined_room": "task-bowser",
                    "task": "pdf validation",
                    "scope": "rendering",
                },
            )
            self.assertEqual(updated["state"], "active")
            self.assertEqual(updated["joined_room"], "task-bowser")
            self.assertEqual(updated["task"], "pdf validation")
            self.assertEqual(updated["scope"], "rendering")


if __name__ == "__main__":
    unittest.main()
