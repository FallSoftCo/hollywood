import sys
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


if __name__ == "__main__":
    unittest.main()
