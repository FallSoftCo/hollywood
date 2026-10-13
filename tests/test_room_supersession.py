import json
from pathlib import Path

import pytest

from hollywood_room.recovery import RETIRED_ERROR, apply_supersessions, event_fingerprint
from hollywood_room.store import Store


def fixture_plan(store):
    source = store.message(
        "old-work", task="lobby", author="you", body="Old work", targets=["maya"], authority="user"
    )
    other = store.message(
        "other-work",
        task="lobby",
        author="you",
        body="Still required",
        targets=["theo"],
        authority="user",
    )
    with store.db:
        evidence_id = store.event("message", "lobby", "maya", "Old work completed and verified")
        store.db.execute("UPDATE delivery SET state='uncertain',error=?", (RETIRED_ERROR,))
    row = store.rows("SELECT * FROM delivery WHERE event=?", (source["id"],))[0]
    entry = {
        "deliveryId": row["id"],
        "eventId": source["id"],
        "task": "lobby",
        "agent": "maya",
        "sourceSha256": event_fingerprint(source),
        "evidence": [
            {"id": evidence_id, "sha256": event_fingerprint(store.get_event(evidence_id))}
        ],
        "reason": "Owner reviewed completion; do not replay obsolete work",
    }
    return {"version": 1, "supersessions": [entry]}, source, other


def test_exact_supersession_precedes_retry_and_preserves_history(tmp_path):
    path = tmp_path / "room.db"
    store = Store(path)
    plan, source, other = fixture_plan(store)
    before = store.events()
    store.close()
    Path(str(path) + ".supersessions.json").write_text(json.dumps(plan))
    store = Store(path)
    rows = store.rows("SELECT event,state,error FROM delivery ORDER BY id")
    assert rows == [
        {"event": source["id"], "state": "superseded", "error": RETIRED_ERROR},
        {"event": other["id"], "state": "queued", "error": None},
    ]
    assert store.events()[:-1] == before
    audit = store.events()[-1]
    assert audit["kind"] == "deliverySuperseded"
    assert audit["data"]["source"] == source["id"]
    cursor = store.snapshot()["cursor"]
    store.close()
    store = Store(path)
    assert store.snapshot()["cursor"] == cursor
    assert (
        store.rows("SELECT state FROM delivery WHERE event=?", (source["id"],))[0]["state"]
        == "superseded"
    )
    store.close()


@pytest.mark.parametrize(
    "change", ["source", "evidence", "identity", "dispatched", "different-error"]
)
def test_mismatch_never_supersedes_or_erases_evidence(tmp_path, change):
    store = Store(tmp_path / "room.db")
    plan, source, _ = fixture_plan(store)
    entry = plan["supersessions"][0]
    if change == "source":
        entry["sourceSha256"] = "0" * 64
    elif change == "evidence":
        entry["evidence"][0]["sha256"] = "0" * 64
    elif change == "identity":
        entry["agent"] = "theo"
    else:
        with store.db:
            if change == "dispatched":
                store.db.execute(
                    "UPDATE delivery SET turn='already-started' WHERE event=?", (source["id"],)
                )
            else:
                store.db.execute(
                    "UPDATE delivery SET error='transport uncertain' WHERE event=?", (source["id"],)
                )
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan))
    before = store.snapshot()
    with pytest.raises(ValueError), store.db:
        apply_supersessions(store, path)
    assert store.snapshot() == before
    store.close()


def test_retire_exact_delivery_notice_without_answer_or_work_mutation(tmp_path):
    path = tmp_path / "room.db"
    store = Store(path)
    source = store.message(
        "private-work",
        task="lobby",
        author="you",
        body="Private work",
        targets=["maya"],
        authority="user",
        visibility="direct:maya",
    )
    with store.db:
        store.db.execute("UPDATE delivery SET state='uncertain',error='Connection lost'")
    alert = store.attention(
        "lobby", "maya", "Could not confirm delivery: Connection lost", visibility="direct:maya"
    )
    real_question = store.attention("lobby", "theo", "Which design should we use?")
    original = store.get_event(alert)
    delivery = store.rows("SELECT * FROM delivery WHERE event=?", (source["id"],))[0]
    plan = {
        "version": 1,
        "retireDeliveryAlerts": [{"id": alert, "sha256": event_fingerprint(original)}],
    }
    plan_path = Path(str(path) + ".supersessions.json")
    plan_path.write_text(json.dumps(plan))
    with store.db:
        apply_supersessions(store, plan_path)
    assert store.get_event(alert) == original
    assert store.rows("SELECT * FROM delivery WHERE event=?", (source["id"],))[0] == delivery
    assert store.rows("SELECT id FROM attention WHERE state='open'") == [{"id": real_question}]
    assert store.attention_card_state(original)["dismissed"] is True
    audit = store.events()[-1]
    assert audit["kind"] == "deliveryAlertRetired"
    assert audit["visibility"] == "direct:maya"
    assert not store.rows("SELECT id FROM event WHERE kind='message' AND id>?", (source["id"],))
    cursor = store.snapshot()["cursor"]
    store.close()
    store = Store(path)
    assert store.snapshot()["cursor"] == cursor
    assert store.rows("SELECT id FROM attention WHERE state='open'") == [{"id": real_question}]
    store.close()


def test_retirement_plan_refuses_unrelated_human_question(tmp_path):
    store = Store(tmp_path / "room.db")
    question = store.attention("lobby", "maya", "Please choose a budget")
    event = store.get_event(question)
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "version": 1,
                "retireDeliveryAlerts": [{"id": question, "sha256": event_fingerprint(event)}],
            }
        )
    )
    with pytest.raises(ValueError, match="evidence mismatch"), store.db:
        apply_supersessions(store, plan)
    assert store.rows("SELECT state FROM attention WHERE id=?", (question,)) == [{"state": "open"}]
    assert store.get_event(question) == event
    store.close()
