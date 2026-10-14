"""Stable room/actor catalog; participation does not grant execution permission."""
import uuid


def provision(store, origin, caller, *, task, agent, role, source, request_id):
    """Communication setup only; never creates bindings, deliveries or grants."""
    import hashlib
    import json

    from .store import Conflict

    team = store.task_team(origin)
    if caller != store.coordinator(origin):
        raise ValueError("Only the team coordinator may provision teammates")
    store.validate_identity(agent)
    store.validate_identity(request_id)
    if not isinstance(role, str) or not role.strip() or len(role) > 2000:
        raise ValueError("Role must contain text and fit within 2000 characters")
    if store.task_team(task) != team:
        raise ValueError("Room does not belong to this team")
    if not isinstance(source, int) or isinstance(source, bool) or not store.rows("SELECT id FROM event WHERE id=?", (source,)):
        raise ValueError("Unknown human source event")
    human = store.get_event(source)
    if human['author'] != 'you' or human['kind'] != 'message' or human['visibility'] != 'room' or human['task'] != origin:
        raise ValueError("Provisioning requires a public human request in the originating room")
    # Separate namespace from transport command IDs; unknown receipts remain intact.
    command = f"provision:{team}:{caller}:{request_id}"
    payload = {"origin": origin, "caller": caller, "task": task, "agent": agent, "role": role, "source": source}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    synchronize(store)
    with store.db:
        previous = store.rows("SELECT * FROM command WHERE id=?", (command,))
        if previous:
            if previous[0]['digest'] != digest:
                raise Conflict("Provision request ID already used with different contents")
            return store.get_event(previous[0]['event'])['data']
        existing = store.rows("SELECT * FROM teammate WHERE id=?", (agent,))
        if existing and existing[0] != {"id": agent, "team": team, "role": role}:
            raise Conflict("Teammate identity or role already differs")
        if not existing:
            store.db.execute("INSERT INTO teammate VALUES(?,?,?)", (agent, team, role))
        actors = store.rows("SELECT id FROM actor_catalog WHERE legacy_agent=?", (agent,))
        actor = actors[0]['id'] if actors else str(uuid.uuid4())
        if not actors:
            store.db.execute("INSERT INTO actor_catalog VALUES(?,?)", (actor, agent))
        room = store.rows("SELECT id FROM room_catalog WHERE task=?", (task,))[0]['id']
        owners = store.rows("SELECT actor FROM room_owner WHERE room=?", (room,))
        if owners and owners[0]['actor'] != actor:
            raise Conflict("Room already has a different accountable owner")
        store.db.execute("INSERT OR IGNORE INTO room_membership VALUES(?,?)", (room, actor))
        store.db.execute("INSERT OR IGNORE INTO room_owner VALUES(?,?)", (room, actor))
        receipt = {"requestId": request_id, "source": source, "coordinator": caller, "team": team,
                       "roomId": room, "actorId": actor, "agent": agent, "executionAdmitted": False}
        event = store.event('provision', origin, caller, 'Teammate registered and joined room', receipt)
        store.db.execute("INSERT INTO command VALUES(?,?,?)", (command, digest, event))
        return receipt


def ensure_schema(store):
    store.db.executescript("""
        CREATE TABLE IF NOT EXISTS room_authority (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1), id TEXT NOT NULL UNIQUE
        );
        CREATE TABLE IF NOT EXISTS room_catalog (
            id TEXT PRIMARY KEY, task TEXT NOT NULL UNIQUE REFERENCES task(id),
            purpose TEXT NOT NULL DEFAULT '', revision INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS actor_catalog (
            id TEXT PRIMARY KEY, legacy_agent TEXT NOT NULL UNIQUE REFERENCES teammate(id)
        );
        CREATE TABLE IF NOT EXISTS room_membership (
            room TEXT NOT NULL REFERENCES room_catalog(id),
            actor TEXT NOT NULL REFERENCES actor_catalog(id), PRIMARY KEY(room,actor)
        );
        CREATE TABLE IF NOT EXISTS room_owner (
            room TEXT PRIMARY KEY REFERENCES room_catalog(id),
            actor TEXT NOT NULL REFERENCES actor_catalog(id)
        );
    """)
    with store.db:
        store.db.execute("INSERT OR IGNORE INTO room_authority VALUES(1,?)", (str(uuid.uuid4()),))


def synchronize(store):
    # Never infer identity from display names or merge threads. No deliveries are created.
    with store.db:
        for row in store.rows("SELECT id FROM task WHERE id NOT IN (SELECT task FROM room_catalog)"):
            store.db.execute("INSERT OR IGNORE INTO room_catalog(id,task) VALUES(?,?)",
                             (str(uuid.uuid4()), row["id"]))
        for row in store.rows("SELECT id FROM teammate WHERE id NOT IN (SELECT legacy_agent FROM actor_catalog)"):
            store.db.execute("INSERT OR IGNORE INTO actor_catalog VALUES(?,?)",
                             (str(uuid.uuid4()), row["id"]))
        store.db.execute("""INSERT OR IGNORE INTO room_membership(room,actor)
            SELECT r.id,a.id FROM room_catalog r JOIN task t ON t.id=r.task
            JOIN team g ON g.id=t.team JOIN actor_catalog a ON a.legacy_agent=g.coordinator""")
        store.db.execute("""INSERT OR IGNORE INTO room_membership(room,actor)
            SELECT r.id,a.id FROM binding b JOIN room_catalog r ON r.task=b.task
            JOIN actor_catalog a ON a.legacy_agent=b.agent""")


def detail(store, room, team):
    rows = store.rows("""SELECT r.*,t.title,t.status,t.team FROM room_catalog r
        JOIN task t ON t.id=r.task WHERE r.id=? AND t.team=?""", (room, team))
    if not rows:
        raise ValueError("Unknown room in this team")
    row = rows[0]
    members = store.rows("""SELECT a.id,a.legacy_agent AS agent,p.role,b.thread,b.status,b.paused
        FROM room_membership m JOIN actor_catalog a ON a.id=m.actor
        JOIN teammate p ON p.id=a.legacy_agent
        LEFT JOIN binding b ON b.task=? AND b.agent=a.legacy_agent
        WHERE m.room=? ORDER BY a.legacy_agent""", (row["task"], room))
    owner = store.rows("""SELECT a.legacy_agent AS agent FROM room_owner o
        JOIN actor_catalog a ON a.id=o.actor WHERE o.room=?""", (room,))
    return {**row, "members": members, "owner": owner[0]['agent'] if owner else None, "contextIsolation": "room-local",
            "membershipGrantsExecution": False}


def default_recipient(store, task):
    team = store.task_team(task)
    lobby = store.rows("SELECT lobby FROM team WHERE id=?", (team,))[0]['lobby']
    if task == lobby:
        return store.coordinator(task)
    owners = store.rows("""SELECT a.legacy_agent AS agent FROM room_owner o
        JOIN room_catalog r ON r.id=o.room JOIN actor_catalog a ON a.id=o.actor
        JOIN teammate p ON p.id=a.legacy_agent
        JOIN task t ON t.id=r.task WHERE r.task=? AND p.team=t.team""", (task,))
    if not owners:
        raise ValueError("Room has no accountable owner; select an explicit recipient")
    return owners[0]['agent']


def catalog(store, team, *, cursor="", limit=100, task=None):
    synchronize(store)
    limit = min(max(int(limit), 1), 100)
    if task is not None:
        store.validate_identity(task)
        rows = store.rows("SELECT r.id FROM room_catalog r JOIN task t ON t.id=r.task WHERE t.team=? AND t.id=?", (team, task))
    else:
        rows = store.rows("""SELECT r.id FROM room_catalog r JOIN task t ON t.id=r.task
            WHERE t.team=? AND r.id>? ORDER BY r.id LIMIT ?""", (team, cursor, limit + 1))
    return {"authorityId": store.rows("SELECT id FROM room_authority")[0]["id"],
            "rooms": [detail(store, row["id"], team) for row in rows[:limit]],
            "next_cursor": rows[limit-1]["id"] if len(rows) > limit else None}


def actors(store, team, *, cursor="", limit=100):
    synchronize(store)
    limit = min(max(int(limit), 1), 100)
    rows = store.rows("""SELECT a.id,a.legacy_agent AS agent,p.role FROM actor_catalog a
        JOIN teammate p ON p.id=a.legacy_agent WHERE p.team=? AND a.id>?
        ORDER BY a.id LIMIT ?""", (team, cursor, limit + 1))
    return {"actors": rows[:limit],
            "next_cursor": rows[limit-1]["id"] if len(rows) > limit else None}


def set_purpose(store, room, team, purpose, revision):
    from .store import Conflict
    if not isinstance(purpose, str) or len(purpose) > 4000:
        raise ValueError("Purpose must fit within 4000 characters")
    detail(store, room, team)
    with store.db:
        changed = store.db.execute("UPDATE room_catalog SET purpose=?,revision=revision+1 WHERE id=? AND revision=?",
                                   (purpose, room, revision)).rowcount
        if not changed:
            raise Conflict("Room revision changed; read before updating")
        store.event("roomMetadata", detail(store, room, team)["task"], "hollywood",
                    "Room purpose updated", {"roomId": room, "revision": revision + 1})
    return detail(store, room, team)


def join(store, room, team, actor):
    row = detail(store, room, team)
    candidates = store.rows("""SELECT a.id,a.legacy_agent FROM actor_catalog a JOIN teammate p ON p.id=a.legacy_agent
        WHERE a.id=? AND p.team=?""", (actor, team))
    if not candidates:
        raise ValueError("Actor is not admitted to this team")
    with store.db:
        inserted = store.db.execute("INSERT OR IGNORE INTO room_membership VALUES(?,?)", (row["id"], actor)).rowcount
        if inserted:
            store.event("membership", row["task"], "hollywood",
                        candidates[0]["legacy_agent"] + " joined room",
                        {"roomId": room, "actorId": actor})
    return detail(store, room, team)


def create(store, team, task, title, purpose):
    """Create a communication context without admitting an agent turn."""
    import time

    from .store import Conflict
    store.validate_identity(task)
    if not isinstance(title, str) or not title.strip() or len(title) > 200:
        raise ValueError("Room title must contain text and fit 200 characters")
    if not isinstance(purpose, str) or len(purpose) > 4000:
        raise ValueError("Room purpose must fit 4000 characters")
    teams = store.rows("SELECT coordinator FROM team WHERE id=?", (team,))
    if not teams:
        raise ValueError("Unknown team")
    synchronize(store)
    existing = store.rows("SELECT r.id,t.team,t.title,r.purpose FROM task t JOIN room_catalog r ON r.task=t.id WHERE t.id=?", (task,))
    if existing:
        row = existing[0]
        if (row["team"], row["title"], row["purpose"]) != (team, title, purpose):
            raise Conflict("Room task ID already exists with different metadata")
        return detail(store, row["id"], team)
    room = str(uuid.uuid4())
    actor = store.rows("SELECT id FROM actor_catalog WHERE legacy_agent=?", (teams[0]["coordinator"],))[0]["id"]
    with store.db:
        store.db.execute("INSERT INTO task(id,title,status,created_at,team) VALUES(?,?,'idle',?,?)", (task, title, int(time.time()), team))
        store.db.execute("INSERT INTO room_catalog(id,task,purpose) VALUES(?,?,?)", (room, task, purpose))
        store.db.execute("INSERT INTO room_membership VALUES(?,?)", (room, actor))
        store.event("roomCreated", task, "hollywood", "Room created without execution", {"roomId": room, "executionAdmitted": False})
    return detail(store, room, team)
