"""Deterministic helpers and optional rollback-only PostgreSQL regression source.

No native/model durability claim: prefix hashes below are disclosed metadata.
Global separately admits execution; this module does not provision a database.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from uuid import uuid4

from hollywood_backbone import context_automatic_refill as active
from hollywood_backbone.store import Conflict, Denied, digest

ID = "fb39418a-957e-42a4-8766-8de32927ea15"
CONTEXT = "5ad2c47b-c89f-40e6-8955-0512d82f2cbb"
PERMIT = "c946ae28-0705-43a6-b069-875b845a6ede"


class Cursor:
    def __init__(self, values):
        self.values = values
        self.statements = []

    def execute(self, sql, args=()):
        self.statements.append((sql, args))
        return self

    def fetchone(self):
        return self.values.pop(0)

    def fetchall(self):
        return self.values.pop(0)


class ActiveRefillTests(unittest.TestCase):
    def test_exact_types(self):
        for value in (True, -1, 2**64, "1"):
            with self.assertRaises(ValueError):
                active.uint(value, "n")
        for value in ("A" * 64, "a" * 63, None):
            with self.assertRaises(ValueError):
                active.sha(value)
        self.assertEqual(active.uint(2**64 - 1, "n"), 2**64 - 1)

    def test_utf8_snapshot_not_character_count(self):
        encoded = '{"value":"λ"}'
        receipt = {"chunks": [{"offset": 0, "content": encoded}],
                   "sha256": hashlib.sha256(encoded.encode()).hexdigest(),
                   "totalBytes": len(encoded.encode())}
        self.assertEqual(active.decoded(receipt), {"value": "λ"})
        with self.assertRaises(Conflict):
            active.decoded({**receipt, "totalBytes": len(encoded)})

    def test_scope_missing_ids_and_cross_purpose(self):
        value = {"operationId": "native-op", "contextId": CONTEXT, "permitId": PERMIT,
                 "materialIds": [ID], "windowId": "old", "historyVersion": 0,
                 "reason": "contextLimit", "phase": "midTurn"}
        binding = {"context_id": CONTEXT, "native_thread": "thread",
                   "active_turn": "turn", "accepting": True, "active_material": [ID]}
        p = {"operationId": "native-op", "action": "active_snapshot", "nativeThread": "other", "nativeTurn": "turn",
             "value": value}
        with self.assertRaises(Conflict):
            active.scope(Cursor([]), None, {"id": ID}, "writer", binding, p)
        p["nativeThread"] = "thread"
        p["value"] = {k: v for k, v in value.items() if k != "permitId"}
        with self.assertRaises(ValueError):
            active.scope(Cursor([]), None, {"id": ID}, "writer", binding, p)

    def test_scope_queued_permit_and_disabled_placement(self):
        binding = {"context_id": CONTEXT, "native_thread": "thread", "active_turn": "turn",
                   "accepting": True, "active_material": [ID], "token_digest": "token"}
        p = {"operationId": "op", "action": "active_snapshot", "nativeThread": "thread", "nativeTurn": "turn",
             "value": {"operationId": "op", "contextId": CONTEXT, "permitId": PERMIT,
                       "materialIds": [ID], "windowId": "old", "historyVersion": 1,
                       "reason": "contextLimit", "phase": "preTurn"}}
        with self.assertRaises(Denied):
            active.scope(Cursor([None, {"digest": "token"}, {"id": ID}]),
                         None, {"id": ID}, "writer", binding, p)
        with self.assertRaises(Conflict):
            active.scope(Cursor([{"host": ID, "placement_revision": 1}, {"digest": "token"}, {"id": ID},
                                 {"state": "queued"}]), None, {"id": ID},
                         "writer", binding, p)

    def test_changed_record_denied_without_read_fence_write(self):
        receipt = {"schema": active.SNAPSHOT_SCHEMA, "binding": {}, "selectionRevision": 0,
                   "chunks": [{"offset": 0, "content": "{}"}], "totalBytes": 2,
                   "sha256": hashlib.sha256(b"{}").hexdigest()}
        c = Cursor([])
        with patch.object(active, "scope", return_value={}), \
                patch.object(active, "records", return_value=({}, [])), \
                patch.object(active, "decoded", return_value={"selection": {}, "records": []}):
            with self.assertRaises(Conflict):
                active.current(c, None, {}, "writer", {"selection_revision": 1}, {}, receipt)
        self.assertEqual(c.statements, [])

    def test_overflow_is_failure_not_truncation(self):
        c = Cursor([None, None, {"point": "cut"}])
        p = {"operationId": "native-op", "value": {
            "historyVersion": 1, "reason": "contextLimit", "phase": "midTurn",
            "materialIds": [ID], "windowId": "old", "operationId": "native-op"}}
        with patch.object(active, "scope", return_value={}), \
                patch.object(active, "records", return_value=({"selectionRevision": 0}, [
                    {"reference": "role", "body": "x" * active.MAX_MANIFEST_BYTES}])), \
                patch.object(active, "obligations", return_value=[]):
            with self.assertRaises(Conflict):
                active.snapshot(c, None, {"id": ID}, "writer", {}, p)
        self.assertFalse(any(sql.startswith("UPDATE") for sql, _ in c.statements))


    def frame(self):
        common = {"operationId": "native-op", "contextId": CONTEXT,
                  "permitId": PERMIT, "materialIds": [ID]}
        binding = {"context_id": CONTEXT, "native_thread": "thread", "active_turn": "turn",
                   "accepting": True, "active_material": [ID], "token_digest": "token",
                   "selection_revision": 0}
        captured = {"agentId": ID, "purpose": "writer", "contextId": CONTEXT,
                    "nativeThread": "thread", "nativeTurn": "turn", **common}
        encoded = active.context_material.wire({"selection": {}, "records": []})
        cut = {"schema": active.SNAPSHOT_SCHEMA, "snapshotOperationId": "native-op",
               "requestOperationId": "native-op", "snapshotReadPoint": "12:13:",
               "sha256": hashlib.sha256(encoded.encode()).hexdigest(),
               "totalBytes": len(encoded.encode()), "selectionRevision": 0,
               "binding": captured, "resetRequest": {**captured, "windowId": "old"},
               "historyVersion": 3, "windowId": "old",
               "chunks": [{"offset": 0, "content": encoded}]}
        p = {"action": "active_installed", "operationId": "installed-op",
             "nativeThread": "thread", "nativeTurn": "turn", "value": {
                 **common, "snapshotOperationId": "native-op",
                 "snapshotReadPoint": cut["snapshotReadPoint"], "snapshotSha256": cut["sha256"],
                 "selectionRevision": 0, "totalBytes": cut["totalBytes"],
                 "nativeActionsSha256": "b" * 64, "previousWindowId": "old", "windowId": "new",
                 "nativeHistoryBytes": 123, "nativeHistorySha256": "a" * 64}}
        return binding, captured, cut, p

    def test_original_request_and_utf8_caps(self):
        binding, _, _, p = self.frame()
        p = {**p, "action": "active_snapshot", "value": {
            k: v for k, v in p["value"].items() if k in active.COMMON}}
        p["value"].update(windowId="old", historyVersion=1, reason="contextLimit", phase="midTurn")
        with self.assertRaises(Conflict):
            active.scope(Cursor([]), None, {"id": ID}, "writer", binding, p)
        self.assertEqual(active.text("λ" * 128, "id"), "λ" * 128)
        with self.assertRaises(ValueError):
            active.text("λ" * 129, "id")
        p["operationId"] = p["value"]["operationId"]
        p["value"]["extraAuthority"] = ID
        with self.assertRaises(ValueError):
            active.scope(Cursor([]), None, {"id": ID}, "writer", binding, p)

    def test_reader_adoption_but_writer_exact_permit(self):
        binding, _, _, p = self.frame()
        added = str(uuid4())
        binding["active_material"] = [ID, added]
        p["value"]["materialIds"] = [ID, added]
        permit = {"state": "running", "context_id": CONTEXT, "host": ID,
                  "native_thread": "thread", "native_turn": "turn", "material_ids": [ID]}
        class Store:
            def readable_event(self, *args):
                return {}
        rows = [{"kind": "input", "source": ID, "state": "pending"}, None] * 2
        with patch.object(active, "authorized", return_value={"host": ID, "placement_revision": 1}):
            result = active.scope(Cursor([permit, *rows]), Store(), {"id": ID}, "reader", binding, p)
            self.assertEqual(result["materialIds"], [ID, added])
            with self.assertRaises(Conflict):
                active.scope(Cursor([permit]), Store(), {"id": ID}, "writer", binding, p)

    def test_cancellation_and_wrong_native_delivery(self):
        binding, _, _, p = self.frame()
        permit = {"state": "running", "context_id": CONTEXT, "host": ID,
                  "native_thread": "thread", "native_turn": "turn", "material_ids": [ID]}
        class Store:
            def readable_event(self, *args):
                return {}
        material = {"kind": "work", "source": ID, "state": "running"}
        delivery = {"state": "running", "host": ID, "cancellation_requested": False,
                    "native_thread": "thread", "native_turn": "turn"}
        with patch.object(active, "authorized", return_value={"host": ID, "placement_revision": 1}):
            for bad in ({**delivery, "cancellation_requested": True},
                        {**delivery, "native_turn": "other"}, {**delivery, "state": "unknown"}):
                c = Cursor([permit, material, bad])
                with self.assertRaises(Conflict):
                    active.scope(c, Store(), {"id": ID}, "writer", binding, p)
                self.assertFalse(any(sql.startswith("UPDATE") for sql, _ in c.statements))

    def test_responder_retained_raw_input_never_read(self):
        binding, _, _, p = self.frame()
        marks = {"selectionRevision": 0, "marks": [{"reference": "event:" + ID}]}
        with patch.object(active, "selected", return_value=marks), \
                patch.object(active, "material", side_effect=lambda *args: {"reference": args[-1]}) as read:
            _, result = active.records(Cursor([{"kind": "need", "state": "ready"}]),
                                       None, {"id": ID}, "responder", binding, p)
        self.assertEqual({r["reference"] for r in result}, {"role", ID, "event:" + ID})
        self.assertEqual(next(r for r in result if r["reference"].startswith("event:"))["access"],
                         "unavailable")
        self.assertEqual({args.args[-1] for args in read.call_args_list}, {"role", ID})

    def test_obligation_overflow_and_inaccessible_pointer(self):
        class Store:
            def readable_event(self, *args):
                raise Denied("revoked")
        row = {"id": ID, "source": ID, "state": "running"}
        self.assertEqual(active.obligations(Cursor([[row]]), Store(), {"id": ID}, "writer"),
                         [{**row, "access": "unavailable"}])
        with self.assertRaises(Conflict):
            active.obligations(Cursor([[row] * 1001]), Store(), {"id": ID}, "writer")

    def test_installed_receipt_read_fences_only_and_duplicate_observation(self):
        binding, captured, cut, p = self.frame()
        c = Cursor([{"result": cut}, None])
        with patch.object(active, "scope", return_value=captured), \
                patch.object(active, "current", return_value=captured):
            result = active.installed(c, None, {"id": ID}, "writer", binding, p)
            self.assertEqual(result["requestOperationId"], "native-op")
            self.assertEqual(result["installedOperationId"], "installed-op")
            self.assertEqual(result["snapshotSha256"], cut["sha256"])
            self.assertEqual(result["resetRequest"], cut["resetRequest"])
            changes = [args for sql, args in c.statements if sql.startswith("UPDATE")]
            self.assertEqual(len(changes), 1)
            # Empty replacement contains no material read proofs. Real-PG
            # qualification covers exact delivered records and stale-source rejection.
            self.assertEqual(changes[0][0].obj, {})
            self.assertEqual(changes[0][1:], (ID, "writer"))
            observed = Cursor([{"result": cut}])
            active.observe(observed, None, {"id": ID}, "writer", binding, p, result)
            self.assertFalse(any(sql.startswith("UPDATE") for sql, _ in observed.statements))
            for key, bad in (("snapshotSha256", "f" * 64), ("snapshotReadPoint", "other"),
                             ("selectionRevision", 1), ("totalBytes", 1),
                             ("previousWindowId", "other"), ("windowId", "old")):
                changed = copy.deepcopy(p)
                changed["value"][key] = bad
                with self.assertRaises(Conflict):
                    active.installed(Cursor([{"result": cut}]), None, {"id": ID},
                                     "writer", binding, changed)
            with self.assertRaises(Conflict):
                active.installed(Cursor([{"result": cut}, {"result": result}]),
                                 None, {"id": ID}, "writer", binding, p)

    def test_original_unsettled_cut_blocks_another_snapshot(self):
        _, captured, _, p = self.frame()
        p = {**p, "action": "active_snapshot", "operationId": "native-op", "value": {
            k: v for k, v in p["value"].items() if k in active.COMMON}}
        p["value"].update(windowId="old", historyVersion=1, reason="contextLimit", phase="midTurn")
        with patch.object(active, "scope", return_value=captured), \
                patch.object(active, "records") as read:
            with self.assertRaises(Conflict):
                active.snapshot(Cursor([{"exists": 1}]), None, {"id": ID}, "writer", {}, p)
            read.assert_not_called()

    def test_same_operation_lookup_is_readonly_and_reauthorizes(self):
        binding, _, cut, _ = self.frame()
        c = Cursor([])
        with patch.object(active, "authorized") as auth, patch.object(active, "material"):
            self.assertIs(active.lookup(c, None, {"id": ID}, "writer", binding, cut), cut)
            auth.assert_called_once()
        self.assertEqual(c.statements, [])
        changed = copy.deepcopy(cut)
        changed["binding"]["nativeThread"] = "another"
        with patch.object(active, "authorized"):
            with self.assertRaises(Denied):
                active.lookup(c, None, {"id": ID}, "writer", binding, changed)
        cut["chunks"] = [{"offset": 0, "content": active.context_material.wire({
            "records": [{"reference": ID}]})}]
        cut["totalBytes"] = len(cut["chunks"][0]["content"].encode())
        cut["sha256"] = hashlib.sha256(cut["chunks"][0]["content"].encode()).hexdigest()
        with patch.object(active, "authorized"), patch.object(active, "material", side_effect=Denied("revoked")):
            with self.assertRaises(Denied):
                active.lookup(c, None, {"id": ID}, "writer", binding, cut)

    def test_active_envelope_caps_do_not_broaden_legacy_calls(self):
        from hollywood_backbone.server import ContextCall
        from pydantic import ValidationError
        base = {"runtimeProtocol": "lax-purpose-runtime/v3", "operationId": "x" * 256,
                "nativeThread": "thread", "nativeTurn": "turn", "value": {}}
        self.assertEqual(ContextCall(**base, action="active_snapshot").operationId, "x" * 256)
        for action, op in (("selection", "x" * 129), ("active_installed", "λ" * 129)):
            with self.assertRaises(ValidationError):
                ContextCall(**{**base, "operationId": op}, action=action)


@unittest.skipUnless(os.environ.get("ACTIVE_REFILL_DATABASE"),
                     "Global-only disposable PostgreSQL integration gate, unrun by default")
class ActiveRefillPostgresTests(unittest.TestCase):
    def test_original_cut_current_refs_and_lifecycle_unchanged(self):
        from psycopg.types.json import Jsonb
        from hollywood_backbone.store import Store
        store = Store(json.loads(Path(os.environ["ACTIVE_REFILL_DATABASE"]).read_text()))
        try:
            with store.pool.connection() as c:
                c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                actor = store.actor(c, os.environ["ACTIVE_REFILL_AGENT"])
                source = os.environ["ACTIVE_REFILL_SOURCE"]
                store.readable_event(c, actor, source)
                binding = c.execute("""SELECT * FROM hb_purpose_context
                    WHERE agent=%s AND purpose='writer' FOR UPDATE""",
                    (actor["id"],)).fetchone()
                # Never borrow a live agent. This is a disclosed synthetic running
                # permit/material fixture, not native admission or a model result.
                self.assertIsNone(binding["active_turn"])
                self.assertFalse(c.execute("""SELECT 1 FROM hb_context_permit WHERE agent=%s
                    AND purpose='writer' AND state IN ('reserved','running')""",
                    (actor["id"],)).fetchone())
                self.assertFalse(c.execute("""SELECT 1 FROM hb_context_selection
                    WHERE agent=%s AND purpose='writer'""", (actor["id"],)).fetchone())
                system = c.execute("SELECT * FROM hb_context_system WHERE agent=%s",
                                   (actor["id"],)).fetchone()
                identity, permit = str(uuid4()), str(uuid4())
                turn = "rollback-active-" + str(uuid4())
                c.execute("""INSERT INTO hb_context_material(id,agent,kind,source,state,value)
                    VALUES(%s,%s,'work',%s,'running',%s)""",
                    (identity, actor["id"], source, Jsonb({"objective": "Synthetic original",
                     "acceptance": "No model proof", "resumeRevision": 7})))
                c.execute("""UPDATE hb_delivery SET state='running',cancellation_requested=false,
                    host=%s,native_thread=%s,native_turn=%s
                    WHERE event=%s AND agent=%s""", (system["host"], binding["native_thread"], turn, source, actor["id"]))
                c.execute("""INSERT INTO hb_context_permit(id,agent,purpose,host,context_id,
                    native_thread,native_turn,operation,fingerprint,material_ids,role_revision,
                    queue_class,state) VALUES(%s,%s,'writer',%s,%s,%s,%s,%s,%s,%s::uuid[],
                    %s,'worker','running')""", (permit, actor["id"], system["host"],
                    binding["context_id"], binding["native_thread"], turn, "fixture:" + permit,
                    digest({}), [identity], system["role_revision"]))
                c.execute("""UPDATE hb_purpose_context SET active_turn=%s,active_material=%s::uuid[],
                    accepting=true,reads=%s WHERE agent=%s AND purpose='writer'""",
                    (turn, [identity], Jsonb({"old": {"revision": 1}}), actor["id"]))
                binding = c.execute("""SELECT * FROM hb_purpose_context
                    WHERE agent=%s AND purpose='writer'""", (actor["id"],)).fetchone()
                common = {"operationId": "native:" + permit, "contextId": str(binding["context_id"]),
                          "permitId": permit, "materialIds": [identity]}
                p = {"operationId": common["operationId"], "action": "active_snapshot",
                     "nativeThread": binding["native_thread"], "nativeTurn": turn,
                     "value": {**common, "windowId": "old", "historyVersion": 42,
                               "reason": "contextLimit", "phase": "midTurn"}}
                cut = active.snapshot(c, store, actor, "writer", binding, p)
                c.execute("INSERT INTO hb_context_operation VALUES(%s,'writer',%s,%s,%s)",
                          (actor["id"], p["operationId"], digest(p), Jsonb(cut)))
                proof = {**common, "snapshotOperationId": p["operationId"],
                         "snapshotReadPoint": cut["snapshotReadPoint"],
                         "snapshotSha256": cut["sha256"], "totalBytes": cut["totalBytes"],
                         "selectionRevision": cut["selectionRevision"],
                         "nativeActionsSha256": hashlib.sha256(b"[]").hexdigest(),
                         "previousWindowId": "old", "windowId": "new",
                         "nativeHistoryBytes": 123, "nativeHistorySha256": "a" * 64}
                installed = {**p, "operationId": "installed:" + permit,
                             "action": "active_installed", "value": proof}
                result = active.installed(c, store, actor, "writer", binding, installed)
                after = c.execute("""SELECT * FROM hb_purpose_context
                    WHERE agent=%s AND purpose='writer'""", (actor["id"],)).fetchone()
                self.assertEqual({k: v for k, v in binding.items() if k != "reads"},
                                 {k: v for k, v in after.items() if k != "reads"})
                self.assertEqual(after["reads"], {})
                self.assertIn("not filesystem inspection", result["verification"])
                c.execute("UPDATE hb_context_material SET revision=revision+1 WHERE id=%s",
                          (identity,))
                with self.assertRaises(Conflict):
                    active.installed(c, store, actor, "writer", after, installed)
                c.rollback()  # Every synthetic state/receipt above is temporary.
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
