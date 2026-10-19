"""Narrow real-QA-SQL and in-process authenticated HTTP checks; rollback only.

No schema migration, listener, dependency install, agent/model admission or
production mutation. Fixtures are mechanical evidence, not model behavior.
"""
import argparse
import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import secrets
import subprocess
import types
from uuid import uuid4

from aiohttp.test_utils import make_mocked_request
import psycopg
from psycopg.rows import dict_row

from hollywood_backbone.server import application, boundary
from hollywood_backbone.store import Store, digest

BASELINE = "ffb2d5bc186060f1667160ec1323f689f842ba70"
MESSAGE_KEYS = {"eventId", "seq", "occurredAt", "authorId", "authorKind", "body", "truncated"}
ACTION_KEYS = {"eventId", "seq", "occurredAt", "authorId", "nativeType", "status", "title", "truncated"}
RECENCY_KEYS = {"eventId", "seq", "occurredAt"}


def uid():
    return str(uuid4())


class CountingConnection:
    def __init__(self, connection):
        self.connection = connection
        self.calls = 0

    def execute(self, *args, **kwargs):
        self.calls += 1
        return self.connection.execute(*args, **kwargs)


class TransactionPool:
    def __init__(self, connection):
        self.c = CountingConnection(connection)

    @contextmanager
    def connection(self):
        # Borrow only: outer forced-rollback transaction owns all effects.
        yield self.c


def legacy_view(catalog):
    result = json.loads(json.dumps(catalog))
    for room in result["rooms"]:
        room.pop("feedSummary", None)
        room["activity"].pop("observedAt", None)
    return result


async def qualify(c):
    source = subprocess.check_output(
        ["git", "-c", "safe.directory=" + str(Path.cwd()),
         "show", BASELINE + ":hollywood_backbone/store.py"], text=True
    )
    baseline = types.ModuleType("hollywood_backbone._catalog_baseline")
    exec(compile(source, "<enrolled-baseline-store>", "exec"), baseline.__dict__)
    store = Store.__new__(Store)
    store.pool = TransactionPool(c)
    app = application(store)
    checks, trace, call_counts = [], [], {}
    authority, foreign = uid(), uid()
    for identity in (authority, foreign):
        c.execute("INSERT INTO hb_authority(id,name) VALUES(%s,'SYNTHETIC catalog rollback')", (identity,))
    actors, tokens = {}, {}
    for name, kind, admin, scope in (
        ("viewer", "human", False, authority), ("agent", "agent", False, authority),
        ("other", "human", False, authority), ("service", "service", False, authority),
        ("admin", "agent", True, authority), ("foreign", "human", True, foreign),
        ("inactive", "human", False, authority),
    ):
        identity = uid()
        actors[name] = {"id": identity, "authority": scope, "kind": kind}
        c.execute("INSERT INTO hb_principal(id,authority,name,kind,administrator,active) VALUES(%s,%s,%s,%s,%s,%s)",
                  (identity, scope, name, kind, admin, name != "inactive"))
        tokens[name] = secrets.token_urlsafe(40)
        c.execute("INSERT INTO hb_token(digest,principal) VALUES(%s,%s)", (digest(tokens[name]), identity))

    def room(name, grant=True, scope=authority):
        identity = uid()
        owner = actors["foreign" if scope == foreign else "agent"]["id"]
        c.execute("INSERT INTO hb_room(id,authority,name,purpose,owner) VALUES(%s,%s,%s,'SYNTHETIC rollback fixture',%s)",
                  (identity, scope, name, owner))
        if scope == authority:
            for who in ("viewer", "agent", "other"):
                c.execute("INSERT INTO hb_membership(authority,room,principal,can_read) VALUES(%s,%s,%s,%s)",
                          (scope, identity, actors[who]["id"], grant))
        return identity

    def event(room_id, who="agent", kind="message", body="visible", data=None, direct=None, at=None):
        identity = Store.event(c, actors[who], room_id, kind, body, data=data,
                               audience="direct" if direct else "room",
                               recipient=actors[direct]["id"] if direct else None)
        if at is not None:
            c.execute("UPDATE hb_event SET created_at=%s WHERE id=%s", (at, identity))
        return c.execute("SELECT * FROM hb_event WHERE id=%s", (identity,)).fetchone()

    def summary(room_id, who="viewer"):
        rows = store.catalog(actors[who]["id"])["rooms"]
        return next(r["feedSummary"] for r in rows if r["id"] == room_id)

    def selected(value, row):
        assert value["eventId"] == str(row["id"])
        assert value["seq"] == row["seq"] and type(value["seq"]) is int
        delta = row["created_at"] - datetime(1970, 1, 1, tzinfo=timezone.utc)
        expected = (delta.days * 86400000000 + delta.seconds * 1000000 + delta.microseconds) // 1000
        assert value["occurredAt"] == expected and type(value["occurredAt"]) is int

    def compatible(who="viewer"):
        identity = actors[who]["id"]
        actual = store.catalog(identity)
        before = baseline.Store.catalog(store, identity)
        assert legacy_view(actual) == legacy_view(before)
        return actual

    def count(label):
        before = store.pool.c.calls
        result = store.catalog(actors["viewer"]["id"])
        call_counts[label] = store.pool.c.calls - before
        return result

    async def http(who="viewer", status=200, token=None):
        headers = {"Authorization": "Bearer " + (token or tokens[who])}
        request = make_mocked_request("GET", "/hollywood/v3/rooms", headers=headers, app=app)
        request._read_bytes = b""
        match = await app.router.resolve(request)
        # Manual in-process routing must establish the real request.app context.
        match.add_app(app)
        request._match_info = match
        response = await boundary(request, match.handler)
        value = json.loads(response.body)
        assert response.status == status, (response.status, status)
        trace.append({"actor": who, "status": response.status, "body": value})
        return value

    assert count("zero")["rooms"] == []
    primary = room("A main")
    assert count("one")["rooms"][0]["feedSummary"] == {
        "version": 1, "latestMessage": None, "latestActivity": None, "recency": None,
    }
    empty = room("B empty")
    private = room("C denied", grant=False)
    foreign_room = room("Foreign", scope=foreign)
    event(private, body="DENIED_ROOM_SENTINEL")
    event(foreign_room, who="foreign", body="FOREIGN_SENTINEL")
    assert {r["id"] for r in compatible()["rooms"]} == {primary, empty}
    assert private in {r["id"] for r in compatible("admin")["rooms"]}
    assert foreign_room not in {r["id"] for r in store.catalog(actors["admin"]["id"])["rooms"]}
    await http("inactive", 403)
    await http(status=403, token="invalid-synthetic-token")
    checks.append("Empty/null categories, authority, denied membership, admin room access and inactive/invalid-token HTTP denial")

    old = event(primary, who="viewer", body="old visible human")
    for kind in ("progress", "result", "question", "approval", "control"):
        event(primary, kind=kind)
    for tag in ("peerCompletion", "collaborationProtocol", "reportScopeProtocol"):
        for kind in ("message", "request"):
            event(primary, kind=kind, data={"type": tag})
    spoof = event(primary, who="service")
    c.execute("UPDATE hb_event SET data=jsonb_set(data,'{authorKind}','\"human\"') WHERE id=%s", (spoof["id"],))
    selected(summary(primary)["latestMessage"], old)
    checks.append("Only eligible human/agent message/request; nonconversation kinds, protocol types and service authors excluded")

    body = "é" * 318 + "🧪" + "e\u0301"
    msg = event(primary, who="agent", kind="request", body=body)
    assert len(body) == 321
    feed = summary(primary)
    selected(feed["latestMessage"], msg)
    assert feed["latestMessage"]["authorKind"] == "agent"
    assert set(feed["latestMessage"]) == MESSAGE_KEYS
    assert feed["latestMessage"]["body"] == body[:320] and feed["latestMessage"]["truncated"] is True
    for size in (0, 319, 320, 321):
        row = event(empty, body="🚀" * size)
        value = summary(empty)
        selected(value["latestMessage"], row)
        assert value["latestMessage"]["body"] == "🚀" * min(size, 320)
        assert value["latestMessage"]["truncated"] is (size > 320)
        assert value["latestActivity"] is None
        selected(value["recency"], row)
    checks.append("Unicode code-point caps: empty/319/320/321, astral emoji, combining marks and explicit truncation")

    activity_data = {"type": "executionActivity", "nativeType": "commandExecution", "status": "completed",
                     "title": "🛰" * 321, "command": "COMMAND_SENTINEL", "output": "OUTPUT_SENTINEL",
                     "cwd": "CWD_SENTINEL", "protocolReceipts": [{"unsafe": "RECEIPT_SENTINEL"}]}
    action = event(primary, kind="progress", body="DO_NOT_COPY_BODY", data=activity_data,
                   at="2030-01-01T00:00:00.999999+00:00")
    feed = summary(primary)
    selected(feed["latestMessage"], msg)
    selected(feed["latestActivity"], action)
    selected(feed["recency"], action)
    assert set(feed["latestActivity"]) == ACTION_KEYS and set(feed["recency"]) == RECENCY_KEYS
    assert feed["latestActivity"]["title"] == "🛰" * 320
    assert feed["latestActivity"]["truncated"] is True
    assert not any(s in json.dumps(feed) for s in ("SENTINEL", "DO_NOT_COPY_BODY"))
    for size in (319, 320):
        action = event(primary, kind="progress", data={**activity_data, "title": "é" * size})
        assert summary(primary)["latestActivity"]["truncated"] is False
    only_action = room("D activity only")
    malformed = event(only_action, kind="message", data={"type": "executionActivity", "title": {"command": "UNSAFE"}, "status": ["UNSAFE"]})
    malformed_feed = summary(only_action)
    assert malformed_feed["latestMessage"] is None
    selected(malformed_feed["latestActivity"], malformed)
    assert all(malformed_feed["latestActivity"][k] is None for k in ("title", "nativeType", "status"))
    assert malformed_feed["latestActivity"]["truncated"] is False
    checks.append("Activity-only and malformed legacy fields; exact allowlist excludes body/data/command/output/cwd/protocol receipts; title cap")

    # Visibility is applied before latest selection, for author and recipient.
    sent = event(primary, who="viewer", direct="other", body="sent by viewer")
    selected(summary(primary)["latestMessage"], sent)
    received = event(primary, who="agent", direct="viewer", body="received by viewer")
    hidden_msg = event(primary, who="agent", direct="other", body="HIDDEN_NEWEST_MESSAGE")
    hidden_action = event(primary, who="agent", kind="progress", direct="other", data={**activity_data, "title": "HIDDEN_NEWEST_ACTIVITY"})
    feed = summary(primary)
    selected(feed["latestMessage"], received)
    selected(feed["latestActivity"], action)
    selected(summary(primary, "agent")["latestActivity"], hidden_action)
    selected(summary(primary, "other")["latestMessage"], hidden_msg)
    admin_feed = summary(primary, "admin")
    selected(admin_feed["latestMessage"], hidden_msg)
    selected(admin_feed["latestActivity"], hidden_action)
    selected(admin_feed["recency"], hidden_action)
    for hidden in (hidden_msg, hidden_action):
        assert str(hidden["id"]) not in json.dumps(feed)
    assert "HIDDEN_NEWEST" not in json.dumps(feed)
    checks.append("Direct author/recipient/admin visibility and hidden-newer fallback in both categories without metadata leakage")

    latest = event(primary, body="newer sequence, older timestamp", at="2020-01-01T00:00:00.123999+00:00")
    feed = summary(primary)
    selected(feed["recency"], latest)
    first_observed = store.catalog(actors["viewer"]["id"])["rooms"][0]["activity"]["observedAt"]
    await asyncio.sleep(0.01)
    c.execute("UPDATE hb_room SET name='A renamed',revision=revision+1 WHERE id=%s", (primary,))
    event(primary, kind="progress", data={"type": "collaborationProtocol"})
    after = store.catalog(actors["viewer"]["id"])["rooms"][0]
    assert after["activity"]["observedAt"] > first_observed and after["feedSummary"] == feed
    selected(feed["recency"], latest)
    checks.append("Recency follows sequence despite inverted timestamps; epoch-ms flooring/provenance; observedAt/rename/protocol bookkeeping stability")

    # Synthetic delivery/attention rows exercise unchanged count contract only.
    for state in ("pending", "claimed", "running", "cancel_requested", "unknown", "completed", "failed", "cancelled"):
        request = event(primary, who="viewer", kind="request", direct="agent")
        delivery = uid()
        c.execute("INSERT INTO hb_delivery(id,authority,event,agent,state,generation,claim_id) VALUES(%s,%s,%s,%s,%s,1,'fixture')",
                  (delivery, authority, request["id"], actors["agent"]["id"], state))
        if state == "running":
            for n, (human, generation, claim) in enumerate((("viewer", 1, "fixture"), ("viewer", 1, "fixture"), ("other", 1, "fixture"), ("viewer", 2, "fixture"), ("viewer", 1, "stale"))):
                c.execute("""INSERT INTO hb_attention(id,authority,delivery,agent,human,room,event,generation,claim_id,
                             native_thread,native_turn,native_slot,method,request,digest)
                             VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,'fixture','fixture',%s,'fixture','{}','fixture')""",
                          (uid(), authority, delivery, actors["agent"]["id"], actors[human]["id"], primary, request["id"], generation, claim, str(n)))
    private_request = event(primary, who="other", kind="request", direct="agent")
    c.execute("INSERT INTO hb_delivery(id,authority,event,agent,state) VALUES(%s,%s,%s,%s,'unknown')",
              (uid(), authority, private_request["id"], actors["agent"]["id"]))
    counts = compatible()["rooms"][0]["activity"]
    assert all(counts[k] == 1 for k in ("pending", "claimed", "running", "cancelRequested", "unknown", "waitingForHuman"))
    assert compatible("admin")["rooms"][0]["activity"]["unknown"] == 2
    stable = summary(primary)
    c.execute("UPDATE hb_delivery SET state='completed' WHERE authority=%s AND state='running'", (authority,))
    assert summary(primary) == stable
    compatible()
    checks.append("Exact baseline legacy fields/counts: five active states, terminal exclusion, private delivery filtering, distinct human waiters, generation/claim fencing and bookkeeping independence")

    for n in range(105):
        room(f"Z {n:03d}")
    full = count("hundred")
    assert len(full["rooms"]) == 100
    assert [r["name"] for r in full["rooms"]] == sorted(r["name"] for r in full["rooms"])
    compatible()
    compatible("admin")
    assert len(set(call_counts.values())) == 1, call_counts
    actual_http = await http()
    assert legacy_view(actual_http) == legacy_view(store.catalog(actors["viewer"]["id"]))
    assert [r["feedSummary"] for r in actual_http["rooms"]] == [r["feedSummary"] for r in full["rooms"]]
    await http("admin")
    assert len((await http("foreign"))["rooms"]) == 1
    checks.append("Unchanged name order/100-room limit against baseline, fixed SQL call count for zero/one/100 rooms, authenticated candidate route and cross-authority HTTP isolation")
    return {"passed": True, "checks": checks, "storeSQLCalls": call_counts,
            "httpChecks": [{"actor": t["actor"], "status": t["status"]} for t in trace],
            "syntheticFixturesOnly": True, "nativeModelCalls": 0, "newListeners": 0,
            "migrations": 0, "baseline": BASELINE, "fixtureAuthorities": [authority, foreign]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-config", type=Path, required=True)
    args = parser.parse_args()
    # Never log configuration, tokens, SQL arguments, or connection exceptions.
    try:
        config = json.loads(args.database_config.read_text())
        connection = psycopg.connect(**config, row_factory=dict_row)
    except Exception as error:
        print(json.dumps({"passed": False, "phase": "QA connection", "errorType": type(error).__name__}))
        raise SystemExit(1) from None
    with connection:
        try:
            with connection.transaction(force_rollback=True):
                connection.execute("SET LOCAL statement_timeout='15s'")
                result = asyncio.run(qualify(connection))
            remaining = connection.execute("SELECT count(*) AS n FROM hb_authority WHERE id=ANY(%s::uuid[])",
                                           (result["fixtureAuthorities"],)).fetchone()["n"]
            assert remaining == 0
            result["rolledBack"] = True
            result["remainingFixtureAuthorities"] = remaining
            print(json.dumps(result, indent=2))
        except Exception as error:
            print(json.dumps({"passed": False, "phase": "qualification", "errorType": type(error).__name__, "rolledBack": True}))
            raise


if __name__ == "__main__":
    main()
