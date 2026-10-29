"""Real PG/authenticated HTTP paused-settlement contract; isolated rollback facts.

No native execution/model behavior is inferred from these synthetic turn facts.
"""
import argparse, asyncio, contextlib, hashlib, json, secrets, time
from pathlib import Path
from uuid import uuid4
import psycopg
from psycopg.rows import dict_row
from aiohttp import ClientSession
from aiohttp.test_utils import TestServer
from hollywood_backbone.server import application
from hollywood_backbone.store import Store, digest

def main(args):
    assert not args.output.exists(), "Inspect existing result; never overwrite"
    cfg = json.loads(Path(args.installation).read_text())
    db = json.loads(args.database.read_text())
    proof = {"at": time.time(), "mode": args.mode, "modelsStarted": 0,
             "nativeProcessesStarted": 0, "productionChanged": False,
             "scope": "Actual PostgreSQL and authenticated HTTP; uncommitted synthetic identity/turn/permit facts, not actual-model or real-turn proof.",
             "cases": []}
    with psycopg.connect(**db, row_factory=dict_row) as c:
        class Borrow:
            @contextlib.contextmanager
            def connection(self):
                with c.transaction():
                    yield c
        store = Store.__new__(Store)
        store.pool = Borrow()
        source = cfg["agentId"]
        identity, context, turn = str(uuid4()), str(uuid4()), "rollback-turn:" + str(uuid4())
        thread, permit = "rollback-thread:" + str(uuid4()), str(uuid4())
        token = secrets.token_urlsafe(32)
        token_digest = digest(token)
        # Copy schema-complete metadata into an uncommitted distinct identity.
        # Never alter the active Global/peer rows or contend on their context lock.
        def clone(table, where, params, overrides):
            from psycopg import sql
            from psycopg.types.json import Jsonb
            query = sql.SQL("INSERT INTO {t} SELECT (jsonb_populate_record(NULL::{t}, to_jsonb(s) || %s::jsonb)).* FROM {t} s WHERE ").format(t=sql.Identifier(table)) + sql.SQL(where)
            result = c.execute(query, (Jsonb(overrides), *params))
            assert result.rowcount == 1, (table, result.rowcount)
        original_principals = c.execute("SELECT count(*) AS n FROM hb_principal").fetchone()["n"]
        clone("hb_principal", "id=%s", (source,),
              {"id": identity, "name": "rollback-paused:" + identity, "administrator": False})
        clone("hb_agent_role", "principal=%s", (source,), {"principal": identity})
        clone("hb_placement", "agent=%s", (source,), {"agent": identity, "enabled": False})
        clone("hb_context_system", "agent=%s", (source,), {"agent": identity})
        b = c.execute("SELECT * FROM hb_purpose_context WHERE agent=%s AND purpose='writer'", (source,)).fetchone()
        clone("hb_token", "digest=%s", (b["token_digest"],),
              {"digest": token_digest, "principal": identity, "revoked": False})
        clone("hb_purpose_context", "agent=%s AND purpose='writer'", (source,),
              {"agent": identity, "context_id": context, "token_digest": token_digest,
               "native_thread": thread, "active_turn": turn, "active_material": [],
               "accepting": True, "reads": {}})
        original_permit = c.execute("SELECT id FROM hb_context_permit WHERE agent=%s AND purpose='writer' ORDER BY created_at DESC LIMIT 1", (source,)).fetchone()
        assert original_permit
        clone("hb_context_permit", "id=%s", (original_permit["id"],),
              {"id": permit, "agent": identity, "context_id": context,
               "native_thread": thread, "native_turn": turn,
               "operation": "rollback-permit:" + str(uuid4()), "state": "running",
               "terminal_status": None, "material_ids": []})

        assert str(store.authenticate(token)["id"]) == identity
        proof["authenticatedSyntheticIdentityVerified"] = True

        async def journey():
            async with TestServer(application(store), host="127.0.0.1") as server:
                async with ClientSession() as client:
                    async def call(action, value=None, *, native_turn=turn,
                                   native_thread=thread, supplied_token=token,
                                   operation=None):
                        payload = {"runtimeProtocol": "lax-purpose-runtime/v3",
                                   "operationId": operation or "rollback-paused:" + str(uuid4()),
                                   "nativeThread": native_thread, "nativeTurn": native_turn,
                                   "action": action, "value": value or {}}
                        url = server.make_url("/hollywood/v3/agents/" + identity + "/contexts/writer")
                        async with client.post(url, json=payload, headers={"Authorization": "Bearer " + supplied_token}) as r:
                            return r.status, await r.json()
                    status, _ = await call("end", {"status": "interrupted", "permitId": permit},
                                           supplied_token="invalid")
                    assert status in (401, 403)
                    proof["cases"].append("invalid_credential_rejected")
                    if args.mode == "baseline":
                        status, result = await call("end", {"status": "interrupted", "permitId": permit})
                        assert status == 403, (status, result)
                        assert c.execute("SELECT state FROM hb_context_permit WHERE id=%s", (permit,)).fetchone()["state"] == "running"
                        proof["cases"].append("old_pause_rejects_original_terminal_and_strands_permit")
                        return
                    for action in ("bind", "pending", "reserve", "begin", "admit", "resume_interrupted", "complete", "publish"):
                        status, result = await call(action)
                        assert status == 403, (action, status, result)
                    proof["cases"].append("paused_placement_denies_intake_work_admission_continuation_publication")
                    for kwargs in ({"native_turn": "foreign-turn"}, {"native_thread": "foreign-thread"}):
                        status, result = await call("end", {"status": "interrupted", "permitId": permit}, **kwargs)
                        assert status == 409, (status, result)
                    status, result = await call("end", {"status": "interrupted", "permitId": str(uuid4())})
                    assert status == 409, (status, result)
                    proof["cases"].append("foreign_turn_thread_permit_cannot_settle_original")
                    status, result = await call("close")
                    assert status == 200, (status, result)
                    assert not c.execute("SELECT accepting FROM hb_purpose_context WHERE agent=%s", (identity,)).fetchone()["accepting"]
                    op = "rollback-terminal:" + str(uuid4())
                    status, result = await call("end", {"status": "interrupted", "permitId": permit}, operation=op)
                    assert status == 200, (status, result)
                    assert result["state"] == "interrupted" and result["admission"]["capacityReleased"] is True
                    assert c.execute("SELECT state FROM hb_context_permit WHERE id=%s", (permit,)).fetchone()["state"] == "released"
                    bnow = c.execute("SELECT * FROM hb_purpose_context WHERE agent=%s", (identity,)).fetchone()
                    assert bnow["active_turn"] is None and not bnow["active_material"] and not bnow["accepting"]
                    proof["cases"].append("original_close_terminal_settle_and_release_existing_capacity_while_paused")
                    status, duplicate = await call("end", {"status": "interrupted", "permitId": permit}, operation=op)
                    assert status == 200 and duplicate == result
                    proof["cases"].append("exact_terminal_receipt_deduplicated_without_releasing_twice")
                    status, result = await call("end", {"status": "interrupted", "permitId": permit})
                    assert status == 409
                    proof["cases"].append("new_terminal_operation_after_settlement_rejected")
                    # Unused reservation release is also an original-receipt action.
                    reservation = str(uuid4())
                    clone("hb_context_permit", "id=%s", (permit,),
                          {"id": reservation, "native_turn": None, "state": "reserved",
                           "terminal_status": None, "operation": "rollback-reserve:" + str(uuid4())})
                    status, result = await call("release_reservation", {"permitId": reservation})
                    assert status == 200 and result["state"] == "released"
                    proof["cases"].append("original_unused_reservation_can_be_released_without_new_execution")
                    c.execute("UPDATE hb_token SET revoked=true WHERE digest=%s", (token_digest,))
                    status, result = await call("close")
                    assert status in (401, 403)
                    proof["cases"].append("revoked_purpose_token_remains_denied")
        asyncio.run(journey())
        c.rollback()
        assert c.execute("SELECT count(*) AS n FROM hb_principal").fetchone()["n"] == original_principals
        assert not c.execute("SELECT 1 FROM hb_principal WHERE id=%s", (identity,)).fetchone()
        assert not c.execute("SELECT 1 FROM hb_context_permit WHERE id=ANY(%s::uuid[])", ([permit],)).fetchone()
        c.rollback()
    proof["rollbackVerified"] = True
    proof["status"] = "passed"
    args.output.write_text(json.dumps(proof, indent=2) + "\n")
    print(json.dumps(proof))

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--database", type=Path, required=True)
    p.add_argument("--installation", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--mode", choices=("baseline", "candidate"), required=True)
    main(p.parse_args())
