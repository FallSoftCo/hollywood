import io
import json
from datetime import datetime
from unittest.mock import patch
from urllib.error import URLError
from zoneinfo import ZoneInfo

import pytest

from hollywood_room.maintainer import TASK, tick


def setup(tmp_path):
    token = tmp_path / "token"
    token.write_text("test-secret")
    return tmp_path / "state", token, "http://localhost:18766", tmp_path / "repo"


def response(task=TASK):
    return io.StringIO(json.dumps({"id": 42, "task": task}))


def test_daily_delivery_and_duplicate_suppression(tmp_path):
    args = setup(tmp_path)
    with patch("hollywood_room.maintainer.urlopen") as send:
        assert (
            tick(*args, datetime(2026, 9, 11, 8, tzinfo=ZoneInfo("America/New_York"))) == "not due"
        )
        send.assert_not_called()
        send.return_value = response()
        assert (
            tick(*args, datetime(2026, 9, 11, 9, tzinfo=ZoneInfo("America/New_York")))
            == "queued maintainer event 42"
        )
        body = json.loads(send.call_args.args[0].data)
        assert body["targets"] == ["maya"]
        assert body["task"] == TASK
        assert len(body["body"]) <= 3500
        assert (
            tick(*args, datetime(2026, 9, 11, 17, tzinfo=ZoneInfo("America/New_York"))) == "not due"
        )
        assert send.call_count == 1
        send.return_value = response()
        tick(*args, datetime(2026, 9, 12, 9, tzinfo=ZoneInfo("America/New_York")))
        assert send.call_count == 2


def test_failed_delivery_keeps_identical_command_across_restart(tmp_path):
    args = setup(tmp_path)
    with patch("hollywood_room.maintainer.urlopen", side_effect=URLError("offline")) as send:
        with pytest.raises(URLError):
            tick(*args, datetime(2026, 9, 11, 9, tzinfo=ZoneInfo("America/New_York")))
        original = send.call_args.args[0].data
    ledger = json.loads((args[0] / "schedule.json").read_text())
    assert "delivered_day" not in ledger
    assert "last_error" in ledger
    with patch("hollywood_room.maintainer.urlopen", return_value=response()) as send:
        tick(*args, datetime(2026, 9, 12, 8, tzinfo=ZoneInfo("America/New_York")))
        assert send.call_args.args[0].data == original
    ledger = json.loads((args[0] / "schedule.json").read_text())
    assert ledger["delivered_day"] == "2026-09-11"
    assert "pending" not in ledger


def test_bad_receipt_does_not_mark_delivery_complete(tmp_path):
    args = setup(tmp_path)
    with (
        patch("hollywood_room.maintainer.urlopen", return_value=response("wrong")),
        pytest.raises(ValueError, match="matching durable"),
    ):
        tick(*args, datetime(2026, 9, 11, 9, tzinfo=ZoneInfo("America/New_York")))
    assert "pending" in json.loads((args[0] / "schedule.json").read_text())
