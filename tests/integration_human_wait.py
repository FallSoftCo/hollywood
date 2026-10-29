"""Captured real-case PG/HTTP wait contract, rollback-only; no model behavior claim."""
import argparse
import asyncio
import contextlib
import copy
import json
import time
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from aiohttp import ClientSession
from aiohttp.test_utils import TestServer
from hollywood_backbone.server import application
from hollywood_backbone.store import Store
from hollywood_backbone.context_goal_dependencies import refresh
from hollywood_backbone.context_system import change


def main(args):
    out = args.output
    assert not out.exists(), "Inspect the original result; do not overwrite it"
    db = json.loads(args.database.read_text())
    manifest = json.loads(args.installation.read_text())
    cfg = json.loads(Path(manifest["config"]).read_text())
    setup = json.loads(args.bootstrap.read_text())
    agent = manifest["agentId"]
    owner = setup["credentials"]["Owner"]["token"]
    proof = {"at": time.time(), "mode": args.mode, "modelsStarted": 0,
             "scope": "Captured real QA records; rollback-only native binding/Goal cut metadata. Authenticated HTTP and actual PG transitions, not a real native/model execution or semantic refresh proof.",
             "cases": []}
    with psycopg.connect(**db, row_factory=dict_row) as c:
        # Borrow one outer transaction: API operations use savepoints; nothing
        # commits to the preserved real-case canonical rows.
        class Borrow:
            @contextlib.contextmanager
            def connection(self):
                with c.transaction():
                    yield c
        store = Store.__new__(Store)
        store.pool = Borrow()
        actor = store.actor(c, agent)
        work0 = c.execute("SELECT * FROM hb_context_material WHERE kind='work' AND agent=%s",
                          (agent,)).fetchone()
        assert work0 and len(work0["value"]["goalResults"]) == 1
        work_id = str(work0["id"])
        goal_id = work0["value"]["goalResults"][0]["goalId"]
        question = c.execute("SELECT * FROM hb_attention WHERE agent=%s AND state='pending'",
                             (agent,)).fetchone()
        assert question
        question_id = str(question["id"])
        original_rows = c.execute("SELECT to_jsonb(m) AS row FROM hb_context_material m ORDER BY id").fetchall()
        original_events = c.execute("SELECT count(*) AS n FROM hb_event").fetchone()["n"]
        original_goal = c.execute("SELECT to_jsonb(g) AS row FROM hb_goal g WHERE id=%s", (goal_id,)).fetchone()
        original_attention = c.execute("SELECT to_jsonb(a) AS row FROM hb_attention a WHERE id=%s", (question_id,)).fetchone()

        def work():
            return c.execute("SELECT * FROM hb_context_material WHERE id=%s", (work_id,)).fetchone()

        def binding(purpose):
            return c.execute("SELECT * FROM hb_purpose_context WHERE agent=%s AND purpose=%s",
                             (agent, purpose)).fetchone()

        def activate(purpose, turn, ids, reads):
            c.execute("""UPDATE hb_purpose_context SET active_turn=%s,accepting=true,
                active_material=%s::uuid[],reads=%s WHERE agent=%s AND purpose=%s""",
                (turn, ids, Jsonb(reads), agent, purpose))

        # Recreate the observed trigger at a deterministic data boundary:
        # intermediate active planning cut, original Writer still executing.
        v = copy.deepcopy(work0["value"])
        v.pop("goalResults", None)
        v["resumeRevision"] = 0
        v["execution"]["resumeRevision"] = 0
        c.execute("UPDATE hb_context_material SET state='running',value=%s WHERE id=%s",
                  (Jsonb(v), work_id))
        c.execute("UPDATE hb_goal SET state='active',plan_revision=plan_revision+1 WHERE id=%s",
                  (goal_id,))
        activate("writer", question["native_turn"], [work_id], {})
        before = work()
        refresh(c, store, actor)
        after = work()
        if args.mode == "baseline":
            assert after["value"]["resumeRevision"] > before["value"]["resumeRevision"]
            proof["cases"].append("old_running_writer_own_planning_cut_queues_spurious_resume")
        else:
            assert after == before
            proof["cases"].append("running_writer_intermediate_planning_cut_does_not_queue_resume")
        # The actual next cut was paused. Old code retains the old actionable
        # active snapshot; candidate observes pause without creating a wake.
        c.execute("UPDATE hb_purpose_context SET active_turn=NULL,accepting=false,active_material='{}' WHERE agent=%s AND purpose='writer'", (agent,))
        c.execute("UPDATE hb_goal SET state='paused' WHERE id=%s", (goal_id,))
        before = work()
        refresh(c, store, actor)
        after = work()
        if args.mode == "baseline":
            assert after["value"]["goalResults"][0]["state"] == "active"
            proof["cases"].append("old_nonactionable_paused_cut_leaves_stale_active_results")
        else:
            assert after["value"]["goalResults"][0]["state"] == "paused"
            assert after["value"]["resumeRevision"] == before["value"]["resumeRevision"]
            proof["cases"].append("paused_cut_is_current_and_creates_no_wake")

        async def journey():
            async with TestServer(application(store), host="127.0.0.1") as server:
                async with ClientSession() as client:
                    async def action(purpose, name, value, operation=None):
                        b = binding(purpose)
                        payload = {"runtimeProtocol": "lax-purpose-runtime/v3",
                                   "operationId": operation or "qa-human-wait:" + str(uuid4()),
                                   "nativeThread": b["native_thread"], "nativeTurn": b["active_turn"] or "runtime",
                                   "action": name, "value": value}
                        async with client.post(server.make_url("/hollywood/v3/agents/" + agent + "/contexts/" + purpose),
                                json=payload, headers={"Authorization": "Bearer " + cfg["tokens"][purpose]}) as r:
                            return r.status, await r.json()

                    turn = question["native_turn"]
                    w = work()
                    activate("writer", turn, [work_id], {work_id: {"revision": w["revision"], "turn": turn}})
                    status, rejected = await action("writer", "wait_human",
                                                   {"workId": work_id, "attentionIds": [str(uuid4())]})
                    assert status == 403, (status, rejected)
                    assert work()["state"] == "running"
                    proof["cases"].append("foreign_or_missing_human_question_cannot_suspend_work")
                    operation = "qa-human-wait:" + str(uuid4())
                    status, waited = await action("writer", "wait_human",
                                                 {"workId": work_id, "attentionIds": [question_id]}, operation)
                    assert status == 200 and waited["state"] == "waiting", (status, waited)
                    assert waited["inferenceControl"] == {
                        "nativeThread": binding("writer")["native_thread"], "nativeTurn": turn,
                        "disposition": "complete"}
                    assert not binding("writer")["accepting"]
                    assert work()["state"] == "waiting"
                    status, duplicate = await action("writer", "wait_human",
                                                     {"workId": work_id, "attentionIds": [question_id]}, operation)
                    assert status == 200 and duplicate == waited
                    proof["cases"].append("exact_human_wait_is_durable_deduplicated_and_tool_settled")

                    # Native completion is fixture metadata here, not fabricated
                    # kernel/native execution evidence.
                    c.execute("UPDATE hb_purpose_context SET active_turn=NULL,active_material='{}' WHERE agent=%s AND purpose='writer'", (agent,))
                    c.execute("UPDATE hb_goal SET state='active' WHERE id=%s", (goal_id,))
                    refresh(c, store, actor)
                    assert work()["state"] == "waiting"
                    w = work()
                    change(c, w, "pending", {**w["value"], "resumeRevision": w["value"]["resumeRevision"] + 1})
                    assert work()["state"] == "waiting"
                    status, pending = await action("writer", "pending", {})
                    assert status == 200 and work_id not in {x["id"] for x in pending["pending"]}
                    proof["cases"].append("goal_and_unrelated_update_wakes_cannot_bypass_human_wait")

                    from hollywood_backbone.attention import scope
                    delivery = c.execute("SELECT * FROM hb_delivery WHERE id=%s", (question["delivery"],)).fetchone()
                    response = {"operationId": "qa-human-answer:" + str(uuid4()),
                                "scope": scope(question, delivery),
                                "response": {"answers": {
                                    q["id"]: {"answers": ["Rollback-only QA answer; no external execution authorized"]}
                                    for q in question["request"]["questions"]}}}
                    route = server.make_url("/hollywood/v3/attention/" + question_id + "/response")
                    async with client.post(route, json=response,
                            headers={"Authorization": "Bearer " + cfg["tokens"]["writer"]}) as r:
                        assert r.status == 403, (r.status, await r.text())
                    async with client.post(route, json=response,
                            headers={"Authorization": "Bearer " + owner}) as r:
                        accepted = await r.json()
                        assert r.status == 200, (r.status, accepted)
                    assert work()["state"] == "waiting"
                    status, pending = await action("writer", "pending", {})
                    assert status == 200 and work_id not in {x["id"] for x in pending["pending"]}
                    proof["cases"].append("purpose_credential_cannot_answer_as_human_and_accepted_answer_alone_does_not_resume_writer")

                    incoming = c.execute("""SELECT * FROM hb_context_material
                        WHERE kind='input' AND value->>'humanQuestionAnswer'='true'
                        ORDER BY created_at DESC LIMIT 1""").fetchone()
                    assert incoming
                    w = work()
                    reader_turn = "rollback-reader:" + str(uuid4())
                    reads = {str(x["id"]): {"revision": x["revision"], "turn": reader_turn} for x in [w, incoming]}
                    activate("reader", reader_turn, [str(incoming["id"])], reads)
                    status, applied = await action("reader", "amend", {
                        "inputId": str(incoming["id"]), "workId": work_id,
                        "workRevision": w["revision"], "body": "Original human answer interpreted; continue the same Work within original authority."})
                    assert status == 200, (status, applied)
                    final = work()
                    assert final["state"] == "pending"
                    assert final["value"]["objective"] == work0["value"]["objective"]
                    assert final["value"]["resumeRevision"] == w["value"]["resumeRevision"] + 1
                    assert c.execute("SELECT state FROM hb_attention WHERE id=%s", (question_id,)).fetchone()["state"] == "closed"
                    status, pending = await action("writer", "pending", {})
                    assert status == 200 and work_id in {x["id"] for x in pending["pending"]}
                    before = work()
                    status, pending = await action("writer", "pending", {})
                    assert status == 200 and work() == before
                    proof["cases"].append("reader_interpretation_releases_same_work_once_with_original_objective")

        if args.mode == "candidate":
            asyncio.run(journey())
        c.rollback()
        assert c.execute("SELECT to_jsonb(m) AS row FROM hb_context_material m ORDER BY id").fetchall() == original_rows
        assert c.execute("SELECT count(*) AS n FROM hb_event").fetchone()["n"] == original_events
        assert c.execute("SELECT to_jsonb(g) AS row FROM hb_goal g WHERE id=%s", (goal_id,)).fetchone() == original_goal
        assert c.execute("SELECT to_jsonb(a) AS row FROM hb_attention a WHERE id=%s", (question_id,)).fetchone() == original_attention
        c.rollback()
    proof["canonicalRowsPreserved"] = True
    proof["productionChanged"] = False
    proof["status"] = "passed"
    out.write_text(json.dumps(proof, indent=2) + "\n")
    print(json.dumps(proof))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--database", type=Path, required=True)
    p.add_argument("--installation", type=Path, required=True)
    p.add_argument("--bootstrap", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--mode", choices=["baseline", "candidate"], required=True)
    main(p.parse_args())
