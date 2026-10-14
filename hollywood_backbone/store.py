"""PostgreSQL authority and atomic communication/execution-intent receipts.

No model, shell, host provisioning or UI code belongs in this store.
"""

import hashlib
import json
import uuid
from pathlib import Path

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool


class Denied(Exception):
    pass


class Conflict(Exception):
    pass


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def uid():
    return str(uuid.uuid4())


def json_value(value):
    return json.loads(json.dumps(value, default=str))


class Store:
    def attention_command(self, actor_id, identity, operation):
        from .attention import command

        return command(self, actor_id, identity, operation)

    def activity_append(self, actor_id, identity, payload):
        from .activity import append

        return append(self, actor_id, identity, payload)

    def attention_create(self, actor_id, identity, payload):
        from .attention import create

        return create(self, actor_id, identity, payload)

    def attention_read(self, actor_id, identity):
        from .attention import read

        return read(self, actor_id, identity)

    def attention_inbox(self, actor_id, room, after, limit):
        from .attention import inbox

        return inbox(self, actor_id, room, after, limit)

    def attention_response(self, actor_id, identity, operation, payload):
        from .attention import respond

        return respond(self, actor_id, identity, operation, payload)

    def attention_acknowledge(self, actor_id, identity, operation, payload):
        from .attention import acknowledge

        return acknowledge(self, actor_id, identity, operation, payload)

    def __init__(self, config):
        self.pool = ConnectionPool(
            kwargs={**config, "row_factory": dict_row}, min_size=1, max_size=8, open=True
        )
        self.pool.wait(timeout=15)

    def close(self):
        self.pool.close()

    def migrate(self):
        with self.pool.connection() as c:
            c.execute("SELECT pg_advisory_xact_lock(1742014101)")
            c.execute(Path(__file__).with_name("schema.sql").read_text())

    def bootstrap(self, authority, name, plan):
        """Local installer operation; never exposed through an anonymous HTTP route."""
        with self.pool.connection() as c:
            c.execute("SELECT pg_advisory_xact_lock(1742014102)")
            if c.execute("SELECT id FROM hb_authority WHERE id=%s", (authority,)).fetchone():
                for credential in plan["credentials"].values():
                    row = c.execute(
                        "SELECT p.id FROM hb_principal p JOIN hb_token t ON t.principal=p.id WHERE p.authority=%s AND p.id=%s AND t.digest=%s AND NOT t.revoked",
                        (authority, credential["principalId"], digest(credential["token"])),
                    ).fetchone()
                    if not row:
                        raise Conflict(
                            "Bootstrap identity/credential differs from accepted installation"
                        )
                room = c.execute(
                    "SELECT id FROM hb_room WHERE id=%s AND authority=%s",
                    (plan["globalRoomId"], authority),
                ).fetchone()
                if not room:
                    raise Conflict("Bootstrap room differs from accepted installation")
                return {
                    "authorityId": authority,
                    "globalRoomId": plan["globalRoomId"],
                    "created": False,
                }
            c.execute("INSERT INTO hb_authority(id,name) VALUES(%s,%s)", (authority, name))
            owner = plan["credentials"]["Owner"]["principalId"]
            global_agent = plan["credentials"]["Global"]["principalId"]
            room = plan["globalRoomId"]
            credentials = plan["credentials"]
            for identity, label, kind in [
                (owner, "Owner", "human"),
                (global_agent, "Global", "agent"),
            ]:
                c.execute(
                    "INSERT INTO hb_principal(id,authority,name,kind,administrator) VALUES(%s,%s,%s,%s,true)",
                    (identity, authority, label, kind),
                )
                token = credentials[label]["token"]
                c.execute("INSERT INTO hb_token VALUES(%s,%s,false)", (digest(token), identity))
            c.execute(
                "INSERT INTO hb_room(id,authority,name,purpose,owner) VALUES(%s,%s,'Global',%s,%s)",
                (room, authority, "Enterprise coordination and administration", global_agent),
            )
            c.execute(
                "INSERT INTO hb_agent_role(principal,authority,primary_role,home_room,purpose) VALUES(%s,%s,'global',%s,%s)",
                (
                    global_agent,
                    authority,
                    room,
                    "Coordinate enterprise work and administer this deployment",
                ),
            )
            return {"authorityId": authority, "globalRoomId": room, "created": True}

    def authenticate(self, token):
        with self.pool.connection() as c:
            row = c.execute(
                "SELECT p.* FROM hb_principal p JOIN hb_token t ON t.principal=p.id WHERE t.digest=%s AND NOT t.revoked AND p.active",
                (digest(token),),
            ).fetchone()
            if not row:
                raise Denied("Unauthenticated")
            return json_value(row)

    @staticmethod
    def actor(c, identity):
        row = c.execute(
            "SELECT * FROM hb_principal WHERE id=%s AND active FOR SHARE", (identity,)
        ).fetchone()
        if not row:
            raise Denied("Principal inactive")
        return row

    @staticmethod
    def admin(actor):
        if not actor["administrator"]:
            raise Denied("Administrator required")

    @staticmethod
    def room(c, actor, identity, action="can_read"):
        row = c.execute(
            "SELECT * FROM hb_room WHERE id=%s AND authority=%s", (identity, actor["authority"])
        ).fetchone()
        if not row:
            raise Denied("Room unavailable")
        if not actor["administrator"]:
            grant = c.execute(
                "SELECT * FROM hb_membership WHERE room=%s AND principal=%s FOR SHARE",
                (identity, actor["id"]),
            ).fetchone()
            if not grant or not grant[action]:
                raise Denied("Room action denied")
        return row

    @staticmethod
    def readable_event(c, actor, identity):
        row = c.execute(
            "SELECT * FROM hb_event WHERE id=%s AND authority=%s", (identity, actor["authority"])
        ).fetchone()
        if not row:
            raise Denied("Event unavailable")
        Store.room(c, actor, row["room"])
        if (
            row["audience"] == "direct"
            and not actor["administrator"]
            and actor["id"] not in (row["author"], row["recipient"])
        ):
            raise Denied("Private audience denied")
        return row

    @staticmethod
    def event(
        c, actor, room, kind, body, *, recipient=None, audience="room", source=None, data=None
    ):
        # This row lock makes sequence order agree with commit order. BIGSERIAL alone
        # would let a reconnect cursor skip a transaction that commits out of order.
        seq = c.execute(
            "UPDATE hb_authority SET next_seq=next_seq+1 WHERE id=%s RETURNING next_seq",
            (actor["authority"],),
        ).fetchone()["next_seq"]
        identity = uid()
        c.execute(
            "INSERT INTO hb_event(id,authority,seq,room,author,recipient,kind,audience,body,source,data) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                identity,
                actor["authority"],
                seq,
                room,
                actor["id"],
                recipient,
                kind,
                audience,
                body,
                source,
                Jsonb({**(data or {}), "authorKind": actor["kind"]}),
            ),
        )
        return identity

    def principal(self, actor_id, identity, name, kind, enrollment_credential):
        with self.pool.connection() as c:
            a = self.actor(c, actor_id)
            self.admin(a)
            row = c.execute("SELECT * FROM hb_principal WHERE id=%s", (identity,)).fetchone()
            if row:
                if (str(row["authority"]), row["name"], row["kind"]) != (
                    str(a["authority"]),
                    name,
                    kind,
                ):
                    raise Conflict("Principal identity conflict")
                if not c.execute(
                    "SELECT principal FROM hb_token WHERE principal=%s AND digest=%s AND NOT revoked",
                    (identity, digest(enrollment_credential)),
                ).fetchone():
                    raise Conflict(
                        "Principal enrollment credential differs; explicit rotation required"
                    )
                return {"principalId": identity, "created": False}
            c.execute(
                "INSERT INTO hb_principal(id,authority,name,kind) VALUES(%s,%s,%s,%s)",
                (identity, a["authority"], name, kind),
            )
            # Installer/connector persists this random credential before submission.
            # A lost reply can reconcile the same identity without losing its key.
            c.execute(
                "INSERT INTO hb_token VALUES(%s,%s,false)",
                (digest(enrollment_credential), identity),
            )
            if kind == "agent":
                c.execute(
                    "INSERT INTO hb_agent_role(principal,authority,primary_role,purpose) VALUES(%s,%s,'worker',%s)",
                    (
                        identity,
                        a["authority"],
                        "Own explicitly addressed work; observe other communication without execution",
                    ),
                )
            return {"principalId": identity, "created": True}

    def principal_read(self, actor_id, identity):
        with self.pool.connection() as c:
            a = self.actor(c, actor_id)
            if str(a["id"]) != identity:
                self.admin(a)
            row = c.execute(
                "SELECT id,authority,name,kind,administrator,active FROM hb_principal WHERE id=%s AND authority=%s",
                (identity, a["authority"]),
            ).fetchone()
            if not row:
                raise Denied("Principal unavailable")
            return json_value(row)

    def role_read(self, actor_id, identity):
        with self.pool.connection() as c:
            a = self.actor(c, actor_id)
            if str(a["id"]) != identity:
                self.admin(a)
            row = c.execute(
                "SELECT * FROM hb_agent_role WHERE principal=%s AND authority=%s",
                (identity, a["authority"]),
            ).fetchone()
            if not row:
                raise Denied("Role unavailable")
            return json_value(row)

    def create_room(self, actor_id, identity, name, purpose, owner):
        with self.pool.connection() as c:
            a = self.actor(c, actor_id)
            self.admin(a)
            target = c.execute(
                "SELECT id FROM hb_principal WHERE id=%s AND authority=%s AND kind='agent' AND active",
                (owner, a["authority"]),
            ).fetchone()
            if not target:
                raise Denied("Room owner must be an active agent in this authority")
            row = c.execute("SELECT * FROM hb_room WHERE id=%s", (identity,)).fetchone()
            if row:
                if (str(row["authority"]), row["name"], row["purpose"], str(row["owner"])) != (
                    str(a["authority"]),
                    name,
                    purpose,
                    owner,
                ):
                    raise Conflict("Room identity conflict")
                return json_value(row)
            c.execute(
                "INSERT INTO hb_room(id,authority,name,purpose,owner) VALUES(%s,%s,%s,%s,%s)",
                (identity, a["authority"], name, purpose, owner),
            )
            c.execute(
                "INSERT INTO hb_membership(authority,room,principal,can_read,can_post,can_request) VALUES(%s,%s,%s,true,true,true)",
                (a["authority"], identity, owner),
            )
            c.execute(
                "UPDATE hb_agent_role SET home_room=%s,revision=revision+1 WHERE principal=%s AND home_room IS NULL",
                (identity, owner),
            )
            return json_value(
                c.execute("SELECT * FROM hb_room WHERE id=%s", (identity,)).fetchone()
            )

    def membership(self, actor_id, room, principal, grants, revision):
        with self.pool.connection() as c:
            a = self.actor(c, actor_id)
            self.admin(a)
            self.room(c, a, room)
            if not c.execute(
                "SELECT id FROM hb_principal WHERE id=%s AND authority=%s",
                (principal, a["authority"]),
            ).fetchone():
                raise Denied("Foreign principal")
            row = c.execute(
                "SELECT * FROM hb_membership WHERE room=%s AND principal=%s FOR UPDATE",
                (room, principal),
            ).fetchone()
            wanted = [grants["can_read"], grants["can_post"], grants["can_request"]]
            if row and [row[k] for k in ["can_read", "can_post", "can_request"]] == wanted:
                return json_value(row)
            if (row["revision"] if row else 0) != revision:
                raise Conflict("Membership revision changed")
            c.execute(
                "INSERT INTO hb_membership(authority,room,principal,can_read,can_post,can_request) VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(room,principal) DO UPDATE SET can_read=EXCLUDED.can_read,can_post=EXCLUDED.can_post,can_request=EXCLUDED.can_request,revision=hb_membership.revision+1",
                (a["authority"], room, principal, *wanted),
            )
            return json_value(
                c.execute(
                    "SELECT * FROM hb_membership WHERE room=%s AND principal=%s", (room, principal)
                ).fetchone()
            )

    def catalog(self, actor_id):
        with self.pool.connection() as c:
            a = self.actor(c, actor_id)
            rows = c.execute(
                "SELECT r.* FROM hb_room r WHERE r.authority=%s AND (%s OR EXISTS(SELECT 1 FROM hb_membership m WHERE m.room=r.id AND m.principal=%s AND m.can_read)) ORDER BY r.name LIMIT 100",
                (a["authority"], a["administrator"], a["id"]),
            ).fetchall()
            return {"authorityId": str(a["authority"]), "rooms": json_value(rows)}

    def room_detail(self, actor_id, identity):
        with self.pool.connection() as c:
            a = self.actor(c, actor_id)
            room = self.room(c, a, identity)
            own = c.execute(
                "SELECT * FROM hb_membership WHERE room=%s AND principal=%s", (identity, a["id"])
            ).fetchone()
            permissions = {
                key: bool(a["administrator"] or own and own[key])
                for key in ("can_read", "can_post", "can_request")
            }
            actors = c.execute(
                """SELECT p.id,p.name,p.kind,r.primary_role,r.home_room,r.purpose
                        FROM hb_principal p LEFT JOIN hb_agent_role r ON r.principal=p.id
                        WHERE p.authority=%s AND p.active AND (p.id=%s OR EXISTS
                         (SELECT 1 FROM hb_membership m WHERE m.room=%s AND m.principal=p.id AND m.can_read))
                        ORDER BY p.name,p.id""",
                (a["authority"], room["owner"], identity),
            ).fetchall()
            work = c.execute(
                """SELECT d.*,e.author AS requester,e.seq AS request_seq,
                          (e.author=%s OR %s) AS can_cancel
                          FROM hb_delivery d JOIN hb_event e ON e.id=d.event
                          WHERE d.authority=%s AND e.room=%s
                          AND d.state IN ('pending','claimed','running','cancel_requested','unknown')
                          AND (%s OR e.audience='room' OR e.author=%s OR e.recipient=%s)
                          ORDER BY e.seq LIMIT 100""",
                (
                    a["id"],
                    a["administrator"],
                    a["authority"],
                    identity,
                    a["administrator"],
                    a["id"],
                    a["id"],
                ),
            ).fetchall()
            return {
                "authorityId": str(a["authority"]),
                "room": json_value(room),
                "permissions": permissions,
                "actors": json_value(actors),
                "deliveries": json_value(work),
            }

    def post(self, actor_id, room_id, operation, payload):
        with self.pool.connection() as c:
            a = self.actor(c, actor_id)
            kind, audience = payload["kind"], payload["audience"]
            room = self.room(c, a, room_id, "can_request" if kind == "request" else "can_post")
            fingerprint = digest({"roomId": room_id, **payload})
            # Serialize command identity before its first acceptance, including requests
            # to different rooms. Existing receipts retain their original resolution.
            c.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                (str(a["id"]) + ":" + operation,),
            )
            old = c.execute(
                "SELECT * FROM hb_command WHERE principal=%s AND operation=%s", (a["id"], operation)
            ).fetchone()
            if old:
                if old["digest"] != fingerprint:
                    raise Conflict("Operation ID reused with changed contents")
                self.readable_event(c, a, old["event"])
                return self.receipt(c, old["event"])
            recipient = payload.get("recipientId")
            if kind == "request" and not recipient:
                recipient = str(room["owner"])
            if recipient:
                target = c.execute(
                    "SELECT * FROM hb_principal WHERE id=%s AND authority=%s AND active",
                    (recipient, a["authority"]),
                ).fetchone()
                if not target or (kind == "request" and target["kind"] != "agent"):
                    raise Denied("Invalid execution recipient")
                if (
                    not target["administrator"]
                    and not c.execute(
                        "SELECT 1 FROM hb_membership WHERE room=%s AND principal=%s AND can_read",
                        (room_id, recipient),
                    ).fetchone()
                ):
                    raise Denied("Recipient cannot access the room")
            if audience == "direct" and not recipient:
                raise Conflict("Private message requires an explicit recipient")
            source = payload.get("sourceEventId")
            if source:
                parent = self.readable_event(c, a, source)
                if parent["audience"] == "direct":
                    original = {str(parent["author"]), str(parent["recipient"])}
                    reply = {str(a["id"]), recipient}
                    if str(parent["room"]) != room_id or audience != "direct" or reply != original:
                        raise Denied("Private source cannot expand its audience")
            event = self.event(
                c,
                a,
                room_id,
                kind,
                payload["body"],
                recipient=recipient,
                audience=audience,
                source=source,
            )
            c.execute(
                "INSERT INTO hb_command VALUES(%s,%s,%s,%s,%s)",
                (a["authority"], a["id"], operation, fingerprint, event),
            )
            if kind == "request":
                c.execute(
                    "INSERT INTO hb_delivery(id,authority,event,agent) VALUES(%s,%s,%s,%s)",
                    (uid(), a["authority"], event, recipient),
                )
            return self.receipt(c, event)

    @staticmethod
    def receipt(c, event):
        e = c.execute("SELECT * FROM hb_event WHERE id=%s", (event,)).fetchone()
        deliveries = c.execute("SELECT * FROM hb_delivery WHERE event=%s", (event,)).fetchall()
        return {"accepted": True, "event": json_value(e), "deliveries": json_value(deliveries)}

    def command(self, actor_id, operation):
        with self.pool.connection() as c:
            a = self.actor(c, actor_id)
            row = c.execute(
                "SELECT * FROM hb_command WHERE principal=%s AND operation=%s", (a["id"], operation)
            ).fetchone()
            if not row:
                raise Denied("Command unavailable")
            self.readable_event(c, a, row["event"])
            return self.receipt(c, row["event"])

    def history(self, actor_id, room, after, limit, before=None):
        with self.pool.connection() as c:
            a = self.actor(c, actor_id)
            self.room(c, a, room)
            ceiling = c.execute(
                "SELECT next_seq FROM hb_authority WHERE id=%s", (a["authority"],)
            ).fetchone()["next_seq"]
            if before is not None:
                rows = c.execute(
                    "SELECT * FROM hb_event WHERE room=%s AND seq<%s AND seq<=%s AND (%s OR audience='room' OR author=%s OR recipient=%s) ORDER BY seq DESC LIMIT %s",
                    (room, before, ceiling, a["administrator"], a["id"], a["id"], limit + 1),
                ).fetchall()
                more = len(rows) > limit
                rows = list(reversed(rows[:limit]))
                return {
                    "authorityId": str(a["authority"]),
                    "roomId": room,
                    "events": json_value(rows),
                    "nextCursor": ceiling,
                    "olderCursor": rows[0]["seq"] if more else None,
                }
            rows = c.execute(
                "SELECT * FROM hb_event WHERE room=%s AND seq>%s AND seq<=%s AND (%s OR audience='room' OR author=%s OR recipient=%s) ORDER BY seq LIMIT %s",
                (room, after, ceiling, a["administrator"], a["id"], a["id"], limit),
            ).fetchall()
            return {
                "authorityId": str(a["authority"]),
                "roomId": room,
                "events": json_value(rows),
                "nextCursor": rows[-1]["seq"] if len(rows) == limit else ceiling,
            }

    def placement(self, actor_id, host, name, fingerprint, capabilities, agent):
        with self.pool.connection() as c:
            a = self.actor(c, actor_id)
            self.admin(a)
            if not c.execute(
                "SELECT id FROM hb_principal WHERE id=%s AND authority=%s AND kind='agent'",
                (agent, a["authority"]),
            ).fetchone():
                raise Denied("Agent unavailable")
            old = c.execute("SELECT * FROM hb_host WHERE id=%s", (host,)).fetchone()
            if old and (str(old["authority"]), old["identity_fingerprint"]) != (
                str(a["authority"]),
                fingerprint,
            ):
                raise Conflict("Host identity changed")
            c.execute(
                "INSERT INTO hb_host VALUES(%s,%s,%s,%s,%s) ON CONFLICT(id) DO NOTHING",
                (host, a["authority"], name, fingerprint, Jsonb(capabilities)),
            )
            old = c.execute(
                "SELECT * FROM hb_placement WHERE agent=%s FOR UPDATE", (agent,)
            ).fetchone()
            if old and str(old["host"]) != host:
                raise Conflict("Agent already placed; fenced transfer required")
            c.execute(
                "INSERT INTO hb_placement(authority,agent,host) VALUES(%s,%s,%s) ON CONFLICT(agent) DO NOTHING",
                (a["authority"], agent, host),
            )
            return {"agentId": agent, "hostId": host, "enrolled": True}

    def claim(self, actor_id, operation, host):
        with self.pool.connection() as c:
            a = self.actor(c, actor_id)
            if a["kind"] != "agent":
                raise Denied("Only an execution connector may claim work")
            c.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (str(a["id"]),))
            placement = c.execute(
                "SELECT * FROM hb_placement WHERE agent=%s AND host=%s AND enabled FOR SHARE",
                (a["id"], host),
            ).fetchone()
            if not placement:
                raise Denied("Connector host not enrolled for this agent")
            previous = c.execute(
                "SELECT * FROM hb_claim WHERE agent=%s AND operation=%s", (a["id"], operation)
            ).fetchone()
            if previous:
                if str(previous["host"]) != host:
                    raise Conflict("Claim host changed")
                if previous["delivery"]:
                    row = c.execute(
                        "SELECT e.room FROM hb_delivery d JOIN hb_event e ON e.id=d.event WHERE d.id=%s",
                        (previous["delivery"],),
                    ).fetchone()
                    self.room(c, a, row["room"])
                return self.claim_receipt(c, previous["delivery"])
            busy = c.execute(
                "SELECT id FROM hb_delivery WHERE agent=%s AND state IN ('claimed','running','unknown','cancel_requested')",
                (a["id"],),
            ).fetchone()
            row = (
                None
                if busy
                else c.execute(
                    "SELECT d.* FROM hb_delivery d JOIN hb_event e ON e.id=d.event WHERE d.agent=%s AND d.state='pending' ORDER BY e.seq FOR UPDATE OF d SKIP LOCKED LIMIT 1",
                    (a["id"],),
                ).fetchone()
            )
            identity = row["id"] if row else None
            if row:
                self.room(
                    c,
                    a,
                    c.execute("SELECT room FROM hb_event WHERE id=%s", (row["event"],)).fetchone()[
                        "room"
                    ],
                )
                c.execute(
                    "UPDATE hb_delivery SET state='claimed',host=%s,claim_id=%s,generation=generation+1,revision=revision+1,updated_at=clock_timestamp() WHERE id=%s",
                    (host, operation, identity),
                )
            c.execute(
                "INSERT INTO hb_claim VALUES(%s,%s,%s,%s)", (a["id"], operation, host, identity)
            )
            return self.claim_receipt(c, identity)

    def connector_pending(self, actor_id, host):
        """Read-only queue projection. Polling it creates no claim or model work."""
        with self.pool.connection() as c:
            a = self.actor(c, actor_id)
            placement = c.execute(
                "SELECT * FROM hb_placement WHERE agent=%s AND host=%s",
                (a["id"], host),
            ).fetchone()
            if a["kind"] != "agent" or not placement:
                raise Denied("Connector not enrolled")
            busy = c.execute(
                "SELECT id,state,native_thread,native_turn FROM hb_delivery WHERE agent=%s AND state IN ('claimed','running','unknown','cancel_requested') ORDER BY updated_at LIMIT 1",
                (a["id"],),
            ).fetchone()
            pending = c.execute(
                "SELECT d.id FROM hb_delivery d JOIN hb_event e ON e.id=d.event WHERE d.agent=%s AND d.state='pending' ORDER BY e.seq LIMIT 1",
                (a["id"],),
            ).fetchone()
            return json_value({"enabled": placement["enabled"], "busy": busy, "pending": pending})

    @staticmethod
    def claim_receipt(c, identity):
        if identity is None:
            return {"delivery": None}
        d = c.execute("SELECT * FROM hb_delivery WHERE id=%s", (identity,)).fetchone()
        e = c.execute("SELECT * FROM hb_event WHERE id=%s", (d["event"],)).fetchone()
        return {"delivery": json_value(d), "request": json_value(e)}

    def transition(self, actor_id, identity, operation, payload):
        with self.pool.connection() as c:
            a = self.actor(c, actor_id)
            d = c.execute(
                "SELECT * FROM hb_delivery WHERE id=%s AND agent=%s FOR UPDATE", (identity, a["id"])
            ).fetchone()
            if not d:
                raise Denied("Delivery unavailable")
            if d["generation"] != payload["generation"] or d["claim_id"] != payload["claimId"]:
                raise Conflict("Stale execution owner")
            for field, supplied in (
                ("native_thread", payload.get("nativeThread")),
                ("native_turn", payload.get("nativeTurn")),
            ):
                if supplied and d[field] and supplied != d[field]:
                    raise Conflict("Native execution pointer changed")
            e = self.readable_event(c, a, d["event"])
            self.room(c, a, e["room"], "can_post")
            previous = c.execute(
                "SELECT * FROM hb_transition WHERE delivery=%s AND operation=%s",
                (identity, operation),
            ).fetchone()
            fingerprint = digest(payload)
            if previous:
                if previous["digest"] != fingerprint:
                    raise Conflict("Transition operation changed")
                return previous["result"]
            allowed = {
                "claimed": {"running", "failed", "unknown", "cancelled"},
                "running": {"completed", "failed", "unknown"},
                "cancel_requested": {"cancelled", "completed", "failed", "unknown"},
                "unknown": {"running", "completed", "failed", "cancelled"},
            }
            state = payload["state"]
            if state not in allowed.get(d["state"], set()):
                raise Conflict("Invalid execution transition")
            if state == "running" and not payload.get("nativeTurn"):
                raise Conflict("Running requires a native turn receipt")
            terminal = state in ("completed", "failed", "cancelled")
            result = None
            if terminal:
                if not payload.get("body"):
                    raise Conflict("Terminal transition requires an explicit result")
                result = self.event(
                    c,
                    a,
                    e["room"],
                    "result",
                    payload["body"],
                    audience=e["audience"],
                    recipient=e["author"] if e["audience"] == "direct" else None,
                    source=e["id"],
                    data={"deliveryId": identity, "state": state, "generation": d["generation"]},
                )
            c.execute(
                "UPDATE hb_delivery SET state=%s,native_thread=COALESCE(%s,native_thread),native_turn=COALESCE(%s,native_turn),result_event=%s,revision=revision+1,updated_at=clock_timestamp() WHERE id=%s",
                (state, payload.get("nativeThread"), payload.get("nativeTurn"), result, identity),
            )
            if terminal:
                # Terminal execution cannot retain an actionable old native prompt.
                # A submitted response's consumption remains a separate fact.
                c.execute(
                    """UPDATE hb_attention SET state=CASE
                          WHEN state='pending' THEN 'expired'
                          WHEN state='native_submitted' THEN 'closed'
                          ELSE 'unknown' END, revision=revision+1,updated_at=clock_timestamp()
                          WHERE delivery=%s AND generation=%s
                          AND state IN ('pending','response_submitted','native_submitted')""",
                    (identity, d["generation"]),
                )
            out = self.claim_receipt(c, identity)
            c.execute(
                "INSERT INTO hb_transition VALUES(%s,%s,%s,%s)",
                (identity, operation, fingerprint, Jsonb(out)),
            )
            return out

    def cancel(self, actor_id, identity):
        with self.pool.connection() as c:
            a = self.actor(c, actor_id)
            d = c.execute(
                "SELECT * FROM hb_delivery WHERE id=%s AND authority=%s FOR UPDATE",
                (identity, a["authority"]),
            ).fetchone()
            if not d:
                raise Denied("Delivery unavailable")
            e = self.readable_event(c, a, d["event"])
            if not a["administrator"] and a["id"] != e["author"]:
                raise Denied("Only requester or administrator may cancel")
            if d["state"] in ("pending", "claimed", "running"):
                # Claimed work may already be executing: cancellation is a request,
                # never evidence that a shell, model turn or external effect stopped.
                state = "cancelled" if d["state"] == "pending" else "cancel_requested"
                c.execute(
                    "UPDATE hb_delivery SET state=%s,revision=revision+1,updated_at=clock_timestamp() WHERE id=%s",
                    (state, identity),
                )
                self.event(
                    c,
                    a,
                    e["room"],
                    "control",
                    "Cancellation " + state,
                    audience=e["audience"],
                    recipient=e["recipient"],
                    source=e["id"],
                    data={"deliveryId": identity, "state": state},
                )
            return self.claim_receipt(c, identity)

    def delivery(self, actor_id, identity):
        with self.pool.connection() as c:
            a = self.actor(c, actor_id)
            d = c.execute(
                "SELECT * FROM hb_delivery WHERE id=%s AND authority=%s", (identity, a["authority"])
            ).fetchone()
            if not d:
                raise Denied("Delivery unavailable")
            e = self.readable_event(c, a, d["event"])
            if not a["administrator"] and a["id"] not in (e["author"], d["agent"]):
                raise Denied("Delivery receipt audience denied")
            return self.claim_receipt(c, identity)

    def execution_context(self, actor_id, identity):
        with self.pool.connection() as c:
            a = self.actor(c, actor_id)
            d = c.execute(
                "SELECT * FROM hb_delivery WHERE id=%s AND agent=%s", (identity, a["id"])
            ).fetchone()
            if not d or a["kind"] != "agent":
                raise Denied("Execution context belongs to another agent")
            e = self.readable_event(c, a, d["event"])
            room = self.room(c, a, e["room"])
            policy = c.execute(
                "SELECT * FROM hb_agent_role WHERE principal=%s", (a["id"],)
            ).fetchone()
            if not policy:
                raise Conflict("Agent role is not provisioned")
            return json_value(
                {
                    "schema": "lax-agent-context/v1",
                    "authorityId": a["authority"],
                    "agentId": a["id"],
                    "primaryRole": policy["primary_role"],
                    "roleRevision": policy["revision"],
                    "homeRoomId": policy["home_room"],
                    "rolePurpose": policy["purpose"],
                    "activeRoomId": e["room"],
                    "roomPurpose": room["purpose"],
                    "roomOwnerId": room["owner"],
                    "requestId": e["id"],
                    "sourceEventId": e["source"],
                    "senderId": e["author"],
                    "senderKind": e["data"].get("authorKind"),
                    "audience": e["audience"],
                    "hostId": d["host"],
                    "generation": d["generation"],
                    "deliveryState": d["state"],
                    "obligation": "respond" if e["kind"] == "request" else "observe",
                    "requiresResponse": e["kind"] == "request" and d["agent"] == a["id"],
                    "observationPolicy": "silent-unless-material",
                    "membershipStartsExecution": False,
                    "peerBodyAuthority": "tool",
                    "communicationAdministrator": a["administrator"],
                    "hostAdministrationVerified": False,
                }
            )

    def role(self, actor_id, agent, primary, purpose, home_room, revision):
        with self.pool.connection() as c:
            a = self.actor(c, actor_id)
            self.admin(a)
            target = c.execute(
                "SELECT * FROM hb_principal WHERE id=%s AND authority=%s AND kind='agent'",
                (agent, a["authority"]),
            ).fetchone()
            if not target or (primary == "global" and not target["administrator"]):
                raise Denied("Role does not confer administrator authority")
            if home_room:
                self.room(c, a, home_room)
            old = c.execute(
                "SELECT * FROM hb_agent_role WHERE principal=%s FOR UPDATE", (agent,)
            ).fetchone()
            if old and (
                old["primary_role"],
                old["purpose"],
                str(old["home_room"]) if old["home_room"] else None,
            ) == (primary, purpose, home_room):
                return json_value(old)
            if (old["revision"] if old else 0) != revision:
                raise Conflict("Role revision changed")
            c.execute(
                "INSERT INTO hb_agent_role(principal,authority,primary_role,purpose,home_room) VALUES(%s,%s,%s,%s,%s) ON CONFLICT(principal) DO UPDATE SET primary_role=EXCLUDED.primary_role,purpose=EXCLUDED.purpose,home_room=EXCLUDED.home_room,revision=hb_agent_role.revision+1",
                (agent, a["authority"], primary, purpose, home_room),
            )
            return json_value(
                c.execute("SELECT * FROM hb_agent_role WHERE principal=%s", (agent,)).fetchone()
            )
