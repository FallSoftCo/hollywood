"""Stable room/actor catalog; participation does not grant execution permission."""
import uuid


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
    return {**row, "members": members, "contextIsolation": "room-local",
            "membershipGrantsExecution": False}


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
