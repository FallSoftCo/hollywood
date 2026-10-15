"""Import an existing Hollywood room catalog/history without admitting old work.

Installation owns credentials and IDs. Source history is immutable evidence;
legacy delivery/attention/native state stays archived and is never replayed.
"""
import argparse
import datetime
import hashlib
import json
import os
import secrets
import shutil
import sqlite3
from pathlib import Path
from uuid import UUID, uuid5

from psycopg.types.json import Jsonb

from .bootstrap import initialize
from .store import Conflict, Store, digest, json_value

PRODUCTS = {
    "team-enterprise": "Global",
    "enterprise-ozzz": "ozzz",
    "enterprise-ozzz-engineering": "ozzz Engineering",
    "enterprise-ozzz-marketing": "ozzz Marketing",
    "enterprise-8bar": "8bar",
    "enterprise-l33": "l33",
    "enterprise-virtue": "virtue",
    "enterprise-piratebae": "PirateBae",
    "enterprise-fallsoftco": "fallsoftco",
    "enterprise-nextcomp": "NextComp",
    "enterprise-lax": "LAX",
    "enterprise-hollywood": "Hollywood",
}


def identity(authority, label):
    return str(uuid5(UUID(authority), label))


def write_intent(path, value):
    with path.open("x") as stream:
        os.chmod(path, 0o600)
        json.dump(value, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    fd = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def migrate(config, source, state, name):
    source = Path(source).resolve()
    state = Path(state)
    state.mkdir(parents=True, mode=0o700, exist_ok=True)
    os.chmod(state, 0o700)
    db = sqlite3.connect("file:" + str(source) + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Source SQLite snapshot failed integrity check")
        authority = str(UUID(db.execute("SELECT id FROM room_authority").fetchone()[0]))
        actors = {row["legacy_agent"]: str(UUID(row["id"]))
                  for row in db.execute("SELECT * FROM actor_catalog")}
        rooms = {row["task"]: dict(row) for row in db.execute("SELECT * FROM room_catalog")}
        if not set(PRODUCTS) <= set(rooms) or "global-steward" not in actors:
            raise ValueError("Existing enterprise catalog is incomplete")
        intent = state / "installation-intent.json"
        if not intent.exists():
            owner = identity(authority, "human:you")
            plan = {
                "authorityId": authority, "name": name,
                "globalRoomId": rooms["team-enterprise"]["id"],
                "credentials": {
                    "Owner": {"principalId": owner, "token": secrets.token_urlsafe(40)},
                    "Global": {"principalId": actors["global-steward"],
                               "token": secrets.token_urlsafe(40)},
                },
                "historicalActors": {},
                "nativeOwners": {},
                "roomIds": {key: rooms[key]["id"] for key in PRODUCTS},
                "sourceSystem": "hollywood/v2",
                "sourceAuthorityId": authority,
                "historicalExecutionAdmission": False,
            }
            for alias, actor in actors.items():
                if alias != "global-steward":
                    plan["historicalActors"][alias] = {
                        "id": actor, "kind": "agent", "name": alias,
                        "token": secrets.token_urlsafe(40),
                    }
            plan["historicalActors"]["hollywood"] = {
                "id": identity(authority, "service:historical-hollywood"),
                "kind": "service", "name": "Historical Hollywood",
                "token": secrets.token_urlsafe(40),
            }
            for task, label in PRODUCTS.items():
                if task == "team-enterprise":
                    continue
                plan["nativeOwners"][task] = {
                    "id": identity(authority, "native-owner:" + task),
                    "kind": "agent", "name": label + " owner",
                    "token": secrets.token_urlsafe(40),
                    "homeRoomId": rooms[task]["id"],
                    "purpose": "Own explicitly addressed " + label
                    + " outcomes, maintain sourced goals and plans, compare alternative "
                    "approaches, delegate bounded work when authorized, and report accepted "
                    "deliveries, blockers and customer outcomes. Observe relevant rooms "
                    "without replying to every event. Do not resume historical work or "
                    "treat room membership as an execution request.",
                }
            write_intent(intent, plan)
        plan = json.loads(intent.read_text())
        if (
            plan["authorityId"] != authority or plan["name"] != name
            or plan["globalRoomId"] != rooms["team-enterprise"]["id"]
            or plan["credentials"]["Global"]["principalId"] != actors["global-steward"]
            or plan["roomIds"] != {key: rooms[key]["id"] for key in PRODUCTS}
        ):
            raise Conflict("Source identities changed from accepted installation intent")
        source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
        archive = state / ("source-" + source_sha + ".sqlite")
        if not archive.exists():
            shutil.copyfile(source, archive)
            archive.chmod(0o600)
        if hashlib.sha256(archive.read_bytes()).hexdigest() != source_sha:
            raise Conflict("Source archive differs")
        initialize(config, state, name)
        store = Store(config)
        imported = existing = 0
        try:
            with store.pool.connection() as c:
                c.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                          ("native-authority:" + authority,))
                owner = plan["credentials"]["Owner"]["principalId"]
                glob = plan["credentials"]["Global"]["principalId"]
                principals = list(plan["historicalActors"].values()) + list(plan["nativeOwners"].values())
                for principal in principals:
                    old = c.execute("SELECT * FROM hb_principal WHERE id=%s",
                                    (principal["id"],)).fetchone()
                    if old:
                        if (str(old["authority"]), old["kind"], old["name"]) != (
                            authority, principal["kind"], principal["name"]
                        ):
                            raise Conflict("Existing imported principal changed")
                    else:
                        c.execute(
                            "INSERT INTO hb_principal(id,authority,name,kind) VALUES(%s,%s,%s,%s)",
                            (principal["id"], authority, principal["name"], principal["kind"]),
                        )
                        c.execute("INSERT INTO hb_token VALUES(%s,%s,false)",
                                  (digest(principal["token"]), principal["id"]))
                for task, label in PRODUCTS.items():
                    room = rooms[task]
                    room_owner = glob if task == "team-enterprise" else plan["nativeOwners"][task]["id"]
                    purpose = room["purpose"] or (label + " goals, work and accepted results")
                    if task == "team-enterprise":
                        # Bootstrap owns this room and its existing exact identity.
                        continue
                    old = c.execute("SELECT authority FROM hb_room WHERE id=%s", (room["id"],)).fetchone()
                    if old and str(old["authority"]) != authority:
                        raise Conflict("Room belongs to another authority")
                    c.execute(
                        """INSERT INTO hb_room(id,authority,name,purpose,owner)
                           VALUES(%s,%s,%s,%s,%s) ON CONFLICT(id) DO NOTHING""",
                        (room["id"], authority, label, purpose, room_owner),
                    )
                for principal in principals:
                    if principal["kind"] != "agent":
                        continue
                    fresh = "homeRoomId" in principal
                    c.execute(
                        """INSERT INTO hb_agent_role
                           (principal,authority,primary_role,home_room,purpose,native_goal_scope)
                           VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(principal) DO NOTHING""",
                        (principal["id"], authority, "product-owner" if fresh else "historical-owner",
                         principal.get("homeRoomId"),
                         principal.get("purpose", "Retained historical identity; no placement or execution resumed."),
                         "role" if fresh else "work"),
                    )
                    if fresh:
                        c.execute(
                            """INSERT INTO hb_membership(authority,room,principal,can_read,can_post,can_request)
                               VALUES(%s,%s,%s,true,true,true) ON CONFLICT(room,principal) DO NOTHING""",
                            (authority, principal["homeRoomId"], principal["id"]),
                        )
                actor_ids = {"you": owner, "global-steward": glob}
                actor_ids.update({key: value["id"] for key, value in plan["historicalActors"].items()})
                kinds = {row["id"]: row["kind"] for row in c.execute(
                    "SELECT id,kind FROM hb_principal WHERE authority=%s", (authority,)
                )}
                # Preserve source event order while never creating hb_delivery rows.
                for row in db.execute("SELECT * FROM event ORDER BY id"):
                    if row["task"] not in PRODUCTS:
                        continue
                    legacy = dict(row)
                    fingerprint = digest(legacy)
                    old = c.execute(
                        """SELECT source_digest FROM hb_imported_event
                           WHERE authority=%s AND source_system='hollywood/v2' AND source_id=%s""",
                        (authority, row["id"]),
                    ).fetchone()
                    if old:
                        if old["source_digest"] != fingerprint:
                            raise Conflict("Previously imported source event changed")
                        existing += 1
                        continue
                    if row["author"] not in actor_ids or row["visibility"] != "room":
                        raise Conflict("Source author/audience needs explicit migration")
                    author = actor_ids[row["author"]]
                    try:
                        legacy_data = json.loads(row["data"]) if row["data"] else {}
                    except (ValueError, TypeError):
                        legacy_data = {"original": row["data"]}
                    data = {
                        "historical": True, "requiresResponse": False,
                        "authorKind": kinds[UUID(author)],
                        "legacy": {"eventId": row["id"], "kind": row["kind"],
                                   "task": row["task"], "data": legacy_data,
                                   "visibility": row["visibility"]},
                        "sourceAuthorityId": authority, "sourceSystem": "hollywood/v2",
                    }
                    event = identity(authority, "v2-event:" + str(row["id"]))
                    seq = c.execute(
                        "UPDATE hb_authority SET next_seq=next_seq+1 WHERE id=%s RETURNING next_seq",
                        (authority,),
                    ).fetchone()["next_seq"]
                    body = row["body"] or ""
                    if row["kind"] != "message":
                        body = "[Historical " + row["kind"] + "] " + body
                    c.execute(
                        """INSERT INTO hb_event(id,authority,seq,room,author,kind,audience,body,data,created_at)
                           VALUES(%s,%s,%s,%s,%s,'message','room',%s,%s,%s)""",
                        (event, authority, seq, rooms[row["task"]]["id"], author, body,
                         Jsonb(data), datetime.datetime.fromtimestamp(
                             row["created_at"], datetime.timezone.utc)),
                    )
                    c.execute(
                        """INSERT INTO hb_imported_event
                           (authority,source_system,source_id,event,source_digest)
                           VALUES(%s,'hollywood/v2',%s,%s,%s)""",
                        (authority, row["id"], event, fingerprint),
                    )
                    imported += 1
                count = c.execute(
                    "SELECT count(*) AS n FROM hb_delivery WHERE authority=%s", (authority,)
                ).fetchone()["n"]
            receipt = {
                "authorityId": authority, "globalRoomId": plan["globalRoomId"],
                "globalAgentId": glob, "rooms": len(PRODUCTS), "nativeOwners": len(plan["nativeOwners"]),
                "sourceSnapshot": str(archive), "sourceSha256": source_sha,
                "eventsImportedThisRun": imported, "eventsAlreadyPresent": existing,
                "historicalDeliveriesReplayed": 0, "nativeDeliveriesCurrentlyPresent": count,
                "historicalLinuxServicesStarted": 0, "sourceAndNativeContextsNotConflated": True,
            }
            path = state / ("import-receipt-" + source_sha + ".json")
            path.write_text(json.dumps(receipt, indent=2) + "\n")
            path.chmod(0o600)
            return receipt
        finally:
            store.close()
    finally:
        db.close()


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-config", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--name", required=True)
    args = parser.parse_args()
    print(json.dumps(json_value(migrate(
        json.loads(args.database_config.read_text()), args.source, args.state, args.name
    ))))


if __name__ == "__main__":
    main()
