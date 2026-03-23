import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import hollywood


class HollywoodTests(unittest.TestCase):
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

    def test_init_db_adds_message_kind_column(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "hollywood.db")
            hollywood.init_db(db_path)
            row = hollywood.insert_message(
                db_path,
                room="main",
                sender_id="sender",
                recipient_id=None,
                message_kind=hollywood.MESSAGE_KIND_BROADCAST,
                body="hello room",
            )
            self.assertEqual(row["message_kind"], hollywood.MESSAGE_KIND_BROADCAST)

    def test_format_line_includes_message_kind(self) -> None:
        formatted = hollywood.format_line(
            {
                "id": 9,
                "created_at": "2026-03-21T00:00:00Z",
                "sender_id": "sender",
                "recipient_id": None,
                "message_kind": hollywood.MESSAGE_KIND_BROADCAST,
                "body": "hello",
            }
        )

        self.assertIn("[broadcast]", formatted)

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
