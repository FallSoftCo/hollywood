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
    def resource_default(self, actor_id, principal, host, payload, *, command=None):
        from .administrative import mutate
        return mutate(self, actor_id, "resource_default", [principal, host, payload], command)

    def _admin_resource_default(self, c, actor, principal, host, payload):
        from .resource_directory import select
        return select(c, actor, principal, host, payload)

    def resource_roots_read(self, actor_id, host=None, after=None, limit=4):
        from .resource_directory import roots
        return roots(self, actor_id, host, after, limit)

    def agent_resources_read(self, actor_id, principal, host, after=None, limit=4):
        from .resource_directory import resources
        return resources(self, actor_id, principal, host, after, limit)

    def resource_default_read(self, actor_id, principal, host):
        from .resource_directory import read_default
        return read_default(self, actor_id, principal, host)

    def delivery_resource_default_read(self, actor_id, delivery):
        from .resource_directory import delivery_default
        return delivery_default(self, actor_id, delivery)

    def physical_agent_grant(self, actor_id, workspace, principal, payload, *, command=None):
        from .administrative import mutate
        return mutate(self, actor_id, "physical_agent_grant", [workspace, principal, payload], command)

    def _admin_physical_agent_grant(self, c, actor, workspace, principal, payload):
        from .agent_resources import grant
        return grant(c, actor, workspace, principal, payload)

    def delivery_targets_read(self, actor_id, identity):
        from .agent_resources import read
        return read(self, actor_id, identity)

    def delivery_edit_call(self, actor_id, payload):
        from .delivery_edits import call
        return call(self, actor_id, payload)

    def delivery_edit_read(self, actor_id, identity):
        from .delivery_edits import read
        return read(self, actor_id, identity)

    def report_scope_read(self, actor_id, identity, payload, *, wire=None):
        from .report_scope import read
        return read(self, actor_id, identity, payload, wire=wire)

    def physical_workspace(self, actor_id, host, identity, payload, *, command=None):
        from .administrative import mutate
        return mutate(self, actor_id, "physical_workspace", [host, identity, payload], command)

    def _admin_physical_workspace(self, c, actor, host, identity, payload):
        from .physical_workspaces import configure
        return configure(c, actor, host, identity, payload)

    def physical_grant(self, actor_id, workspace, work, payload, *, command=None):
        from .administrative import mutate
        return mutate(self, actor_id, "physical_grant", [workspace, work, payload], command)

    def _admin_physical_grant(self, c, actor, workspace, work, payload):
        from .physical_workspaces import grant
        return grant(c, actor, workspace, work, payload)

    def physical_targets_read(self, actor_id, identity):
        from .physical_workspaces import read
        return read(self, actor_id, identity)

    def edit_plan_call(self, actor_id, payload):
        from .edit_plans import call
        return call(self, actor_id, payload)

    def edit_plan_read(self, actor_id, identity):
        from .edit_plans import read
        return read(self, actor_id, identity)

    def mutation_observe(self, actor_id, payload):
        from .edit_plans import observe
        return observe(self, actor_id, payload)

    def collaboration_bridge(self, actor_id, host, payload, *, command=None):
        from .administrative import mutate
        return mutate(self, actor_id, "collaboration_bridge", [host, payload], command)

    def _admin_collaboration_bridge(self, c, actor, host, payload):
        from .collaboration import configure
        return configure(c, actor, host, payload)

    def collaboration_call(self, actor_id, payload):
        from .collaboration import call
        return call(self, actor_id, payload)

    def collaboration_bridge_read(self, actor_id, host):
        from .collaboration import read_bridge
        return read_bridge(self, actor_id, host)

    def collaboration_command(self, actor_id, agent, operation):
        from .collaboration import read_command
        return read_command(self, actor_id, agent, operation)

    def executor_begin(self, actor_id, delivery, payload):
        from .executors import begin
        return begin(self, actor_id, delivery, payload)

    def executor_reserve(self, actor_id, delivery, payload):
        from .executors import begin

        return begin(
            self,
            actor_id,
            delivery,
            {**payload, "nativePid": None, "nativeStart": None},
            reserved=True,
        )

    def executor_attach(self, actor_id, identity, payload):
        from .executors import attach

        return attach(self, actor_id, identity, payload)

    def executor_read(self, actor_id, identity):
        from .executors import read
        return read(self, actor_id, identity)

    def executor_list(self, actor_id, host, after=None, limit=256):
        from .executors import listing
        return listing(self, actor_id, host, after, limit)

    def executor_observe(self, actor_id, host, identity, payload):
        from .executors import observe
        return observe(self, actor_id, host, identity, payload)

    def native_effects(self, actor_id, delivery, after=0, limit=100):
        from .native_effects import listing

        return listing(self, actor_id, delivery, after, limit)

    def administrative_command(self, actor_id, operation):
        from .administrative import read
        return read(self, actor_id, operation)

    def native_control_create(self, actor_id, identity, operation, payload):
        from .native_controls import create
        return create(self, actor_id, identity, operation, payload)

    def native_control_read(self, actor_id, identity):
        from .native_controls import read
        return read(self, actor_id, identity)

    def native_control_pending(self, actor_id, identity, payload):
        from .native_controls import pending
        return pending(self, actor_id, identity, payload)

    def native_control_update(self, actor_id, identity, operation, payload):
        from .native_controls import update
        return update(self, actor_id, identity, operation, payload)

    def portfolio_read(self, actor_id, agent, after=None, limit=100):
        from .portfolio import read

        return read(self, actor_id, agent, after, limit)

    def portfolio_policy_set(self, actor_id, agent, operation, payload):
        from .portfolio import configure

        return configure(self, actor_id, agent, operation, payload)

    def portfolio_dispatch(self, actor_id, host, capabilities):
        from .portfolio import dispatch

        return dispatch(self, actor_id, host, capabilities)

    def host_policy(self, actor_id, host):
        from .admission import policy

        return policy(self, actor_id, host)

    def host_policy_set(self, actor_id, host, operation, payload):
        from .admission import configure

        return configure(self, actor_id, host, operation, payload)

    def host_sample(self, actor_id, host, operation, payload):
        from .admission import sample

        return sample(self, actor_id, host, operation, payload)

    def connector_observe(self, actor_id, host, capabilities):
        from .admission import observe

        return observe(self, actor_id, host, capabilities)

    def decision_write(self, actor_id, room, operation, payload, *, wire=None):
        from .memory import write

        return write(self, actor_id, room, operation, payload, wire=wire)

    def decision_list(self, actor_id, room, after, limit, goal=None):
        from .memory import listing

        return listing(self, actor_id, room, after, limit, goal)

    def briefing(self, actor_id, delivery):
        from .memory import briefing

        return briefing(self, actor_id, delivery)

    def work_attempts(self, actor_id, work, after, limit):
        from .attempts import read

        return read(self, actor_id, work, after, limit)

    def agent_context(self, actor_id, agent):
        from .agent_context import read
        return read(self, actor_id, agent)

    def agent_execution_binding(self, actor_id, identity, payload):
        from .agent_context import bind
        return bind(self, actor_id, identity, payload)

    def communication_set(self, actor_id, agent, credential_digest, allow_requests, *, command=None):
        from .agent_context import grant
        return grant(self, actor_id, agent, credential_digest, allow_requests, command=command)

    def _admin_communication(self, c, a, agent, credential_digest, allow_requests):
        from .agent_context import admin_grant
        return admin_grant(self, c, a, agent, credential_digest, allow_requests)

    def native_goal_binding(self, actor_id, identity, payload):
        from .work import native_binding

        return native_binding(self, actor_id, identity, payload)

    def workflow(self, actor_id, action, identity, operation, payload, *, wire=None):
        from .work import mutation

        return mutation(self, actor_id, action, identity, operation, payload, wire=wire)

    def goal_read(self, actor_id, identity):
        from .work import read

        return read(self, actor_id, identity)

    def work_read(self, actor_id, identity):
        from .work import read

        return read(self, actor_id, identity, work=True)

    def goal_list(self, actor_id, room, after, limit):
        from .work import listing

        return listing(self, actor_id, room, after, limit)

    def work_list(self, actor_id, identity, after, limit, by_goal=False):
        from .work import work_listing

        return work_listing(self, actor_id, identity, after, limit, by_goal)

    def review_dispatch(self, actor_id, host, capabilities=()):
        from .work import dispatch_reviews

        return dispatch_reviews(self, actor_id, host, capabilities)

    def supervision(self, actor_id, after, limit):
        from .work import supervision

        return supervision(self, actor_id, after, limit)

    def supervision_ack(self, actor_id, seq, revision):
        from .work import acknowledge

        return acknowledge(self, actor_id, seq, revision)

    def inbox(self, actor_id, after, limit, pending=False):
        from .inbox import feed

        return feed(self, actor_id, after, limit, pending)

    def inbox_event(self, actor_id, identity):
        from .inbox import read

        return read(self, actor_id, identity)

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
                "INSERT INTO hb_agent_role(principal,authority,primary_role,home_room,purpose,native_goal_scope) VALUES(%s,%s,'global',%s,%s,'role')",
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
        from .native_effects import enter

        enter(c, identity)
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

    def principal(self, actor_id, identity, name, kind, enrollment_credential, *, command=None):
        from .administrative import mutate
        return mutate(self, actor_id, 'principal', [identity, name, kind, enrollment_credential], command)

    def _admin_principal(self, c, a, identity, name, kind, enrollment_credential):
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

    def create_room(self, actor_id, identity, name, purpose, owner, *, command=None):
        from .administrative import mutate
        return mutate(self, actor_id, 'create_room', [identity, name, purpose, owner], command)

    def _admin_create_room(self, c, a, identity, name, purpose, owner):
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

    def membership(self, actor_id, room, principal, grants, revision, *, command=None):
        from .administrative import mutate
        return mutate(self, actor_id, 'membership', [room, principal, grants, revision], command)

    def _admin_membership(self, c, a, room, principal, grants, revision):
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
                """WITH readable_rooms AS (
                       SELECT r.* FROM hb_room r WHERE r.authority=%s
                       AND (%s OR EXISTS(SELECT 1 FROM hb_membership m
                         WHERE m.room=r.id AND m.principal=%s AND m.can_read))
                       ORDER BY r.name LIMIT 100
                   ), counts AS (
                       SELECT e.room,
                         count(*) FILTER (WHERE d.state='pending') AS pending,
                         count(*) FILTER (WHERE d.state='claimed') AS claimed,
                         count(*) FILTER (WHERE d.state='running') AS running,
                         count(*) FILTER (WHERE d.state='cancel_requested') AS cancelling,
                         count(*) FILTER (WHERE d.state='unknown') AS unknown,
                         count(*) FILTER (WHERE EXISTS(
                           SELECT 1 FROM hb_attention h WHERE h.delivery=d.id
                           AND h.generation=d.generation AND h.claim_id=d.claim_id
                           AND h.state='pending' AND (%s OR h.human=%s)
                         )) AS waiting
                       FROM hb_delivery d JOIN hb_event e ON e.id=d.event
                       JOIN readable_rooms r ON r.id=e.room
                       WHERE d.authority=%s
                       AND d.state IN ('pending','claimed','running','cancel_requested','unknown')
                       AND (%s OR e.audience='room' OR e.author=%s OR e.recipient=%s)
                       GROUP BY e.room
                   )
                   SELECT r.*,jsonb_build_object(
                     'version',1,
                     'observedAt',floor(extract(epoch FROM statement_timestamp())*1000)::bigint,
                     'pending',coalesce(c.pending,0),
                     'claimed',coalesce(c.claimed,0),
                     'running',coalesce(c.running,0),
                     'cancelRequested',coalesce(c.cancelling,0),
                     'unknown',coalesce(c.unknown,0),
                     'waitingForHuman',coalesce(c.waiting,0)
                   ) AS activity
                   FROM readable_rooms r LEFT JOIN counts c ON c.room=r.id
                   ORDER BY r.name""",
                (
                    a["authority"], a["administrator"], a["id"],
                    a["administrator"], a["id"],
                    a["authority"], a["administrator"], a["id"], a["id"],
                ),
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
            from .native_controls import scope as control_scope, human_allowed, capable
            for item in work:
                event = self.readable_event(c, a, item["event"])
                permitted = False
                try:
                    human_allowed(self, c, a, event)
                    permitted = item["state"] == "running" and not item["cancellation_requested"] and capable(c, item)
                except (Denied, Conflict):
                    pass
                item["can_steer"] = permitted
                item["control_scope"] = control_scope(item, event) if permitted else None
            return {
                "authorityId": str(a["authority"]),
                "room": json_value(room),
                "permissions": permissions,
                "actors": json_value(actors),
                "memberships": json_value(
                    c.execute(
                        "SELECT * FROM hb_membership WHERE room=%s AND (%s OR principal=%s) ORDER BY principal",
                        (identity, a["administrator"], a["id"]),
                    ).fetchall()
                ),
                "deliveries": json_value(work),
            }

    def post(self, actor_id, room_id, operation, payload, *, wire=None):
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
            if kind == "request" and a["kind"] == "agent" and str(recipient) == str(a["id"]):
                raise Denied("Self execution requests require an explicit lifecycle policy")
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
            data = {}
            if payload.get("expectsReply"):
                if kind != "request" or a["kind"] != "agent":
                    raise ValueError("Only an explicit agent request may establish a peer reply obligation")
                data["expectsReply"] = True
            if source:
                parent = self.readable_event(c, a, source)
                if parent["audience"] == "direct":
                    original = {str(parent["author"]), str(parent["recipient"])}
                    reply = {str(a["id"]), recipient}
                    if str(parent["room"]) != room_id or audience != "direct" or reply != original:
                        raise Denied("Private source cannot expand its audience")
                if kind == "request" and str(parent["room"]) == room_id:
                    goals = c.execute(
                        """SELECT id FROM hb_goal WHERE authority=%s AND room=%s AND owner=%s
                           AND (source=%s OR id::text=%s) ORDER BY id LIMIT 2""",
                        (
                            a["authority"],
                            room_id,
                            recipient,
                            parent["id"],
                            parent["data"].get("goalReferenceId", parent["data"].get("goalId")),
                        ),
                    ).fetchall()
                    if len(goals) == 1:
                        data["goalReferenceId"] = str(goals[0]["id"])
            event = self.event(
                c,
                a,
                room_id,
                kind,
                payload["body"],
                recipient=recipient,
                audience=audience,
                source=source,
                data=data,
            )
            if kind == "request":
                c.execute(
                    "INSERT INTO hb_delivery(id,authority,event,agent) VALUES(%s,%s,%s,%s)",
                    (uid(), a["authority"], event, recipient),
                )
            result = self.receipt(c, event)
            c.execute(
                """INSERT INTO hb_command(authority,principal,operation,digest,event,wire_request,accepted_result)
                   VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                (a["authority"], a["id"], operation, fingerprint, event, Jsonb(wire), Jsonb(result)),
            )
            return result

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
            event = self.readable_event(c, a, row["event"])
            receipt = self.receipt(c, row["event"])
            if event["data"].get("workflowReceipt"):
                receipt["workflow"] = event["data"]["workflowReceipt"]
            if event["data"].get("decisionReceipt"):
                receipt["decision"] = event["data"]["decisionReceipt"]
            receipt["canonicalCommand"] = json_value({
                "schema": "hollywood-command/v1", "authorityId": row["authority"],
                "actorId": row["principal"], "operationId": row["operation"],
                "semanticDigest": row["digest"], "eventId": row["event"],
                "wireRequest": row["wire_request"],
                "wireDigest": digest(row["wire_request"]) if row["wire_request"] else None,
                "acceptedResult": row["accepted_result"],
                "evidenceComplete": bool(row["wire_request"] and row["accepted_result"]),
            })
            return receipt

    def history(self, actor_id, room, after, limit, before=None, view="all"):
        from .activity import project

        # Both projections read the same canonical records with their original IDs,
        # audience and commit-ordered sequence. Never filter a limited page in Python:
        # a burst of actions must not hide a conversation behind an empty page.
        filters = {
            "all": "",
            "conversation": " AND data->>'type' IS DISTINCT FROM 'executionActivity'"
                " AND data->>'type' IS DISTINCT FROM 'collaborationProtocol'"
                " AND data->>'type' IS DISTINCT FROM 'reportScopeProtocol'"
                " AND NOT (kind='progress' AND (data ? 'collaborationReceipt' OR data ? 'editPlanReceipt'))",
            "activity": " AND data->>'type' = 'executionActivity'",
        }
        if view not in filters:
            raise ValueError("Choose all, conversation or activity")
        selected = filters[view]
        with self.pool.connection() as c:
            a = self.actor(c, actor_id)
            self.room(c, a, room)
            ceiling = c.execute(
                "SELECT next_seq FROM hb_authority WHERE id=%s", (a["authority"],)
            ).fetchone()["next_seq"]
            if before is not None:
                rows = c.execute(
                    "SELECT * FROM hb_event WHERE room=%s AND seq<%s AND seq<=%s "
                    "AND (%s OR audience='room' OR author=%s OR recipient=%s)"
                    + selected + " ORDER BY seq DESC LIMIT %s",
                    (room, before, ceiling, a["administrator"], a["id"], a["id"], limit + 1),
                ).fetchall()
                more = len(rows) > limit
                rows = list(reversed(rows[:limit]))
                return {
                    "authorityId": str(a["authority"]),
                    "roomId": room,
                    "view": view,
                    "events": json_value([project(row, c, a) for row in rows]),
                    "nextCursor": ceiling,
                    "olderCursor": rows[0]["seq"] if more else None,
                }
            rows = c.execute(
                "SELECT * FROM hb_event WHERE room=%s AND seq>%s AND seq<=%s "
                "AND (%s OR audience='room' OR author=%s OR recipient=%s)"
                + selected + " ORDER BY seq LIMIT %s",
                (room, after, ceiling, a["administrator"], a["id"], a["id"], limit),
            ).fetchall()
            return {
                "authorityId": str(a["authority"]),
                "roomId": room,
                "view": view,
                "events": json_value([project(row, c, a) for row in rows]),
                "nextCursor": rows[-1]["seq"] if len(rows) == limit else ceiling,
            }

    def placement(self, actor_id, host, name, fingerprint, capabilities, agent, *, command=None):
        from .administrative import mutate
        return mutate(self, actor_id, 'placement', [host, name, fingerprint, capabilities, agent], command)

    def _admin_placement(self, c, a, host, name, fingerprint, capabilities, agent):
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

    @staticmethod
    def native_role_capability(c, actor, capabilities):
        if "persistent-agent-context/v1" in capabilities:
            return  # Role revisions do not own or replace the agent context.
        role = c.execute(
            "SELECT native_goal_scope,revision FROM hb_agent_role WHERE principal=%s FOR SHARE",
            (actor["id"],),
        ).fetchone()
        if (
            role
            and c.execute(
                """SELECT 1 FROM hb_native_role_goal WHERE principal=%s
               AND (role_revision<>%s OR %s<>'role') LIMIT 1""",
                (actor["id"], role["revision"], role["native_goal_scope"]),
            ).fetchone()
        ):
            raise Conflict("Existing native responsibility requires explicit role reconciliation")
        if (
            role
            and role["native_goal_scope"] == "role"
            and "native-role-goals/v1" not in capabilities
        ):
            raise Conflict("Native role lifecycle connector capability required")

    def claim(self, actor_id, operation, host, capabilities=()):
        from . import admission

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
            self.native_role_capability(c, a, capabilities)
            if not admission.CAPABILITIES <= set(capabilities):
                raise Conflict("Connector upgrade required for host admission")
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
            admission.presence(self, c, a, host, capabilities)
            admission.lock(c, host)
            observed = admission.diagnose(self, c, a, host, capabilities, record=False)
            from .work import claim_candidate

            row = (
                claim_candidate(self, c, a, observed["eligibleDeliveryId"])
                if observed["eligibleDeliveryId"]
                else None
            )
            identity = row["id"] if row else None
            # Lock the selected delivery before its trace, matching cancellation
            # triggers. Observations never lock a delivery after updating a trace.
            admission.record_observation(c, host, observed)
            if row:
                from .cooperation import verify_admission
                from .memory import verify_admission as verify_briefing

                event = self.readable_event(c, a, row["event"])
                verify_briefing(c, event, capabilities)
                verify_admission(self, c, a, event, capabilities)
                self.room(c, a, event["room"])
                admission.reserve(c, host, identity, observed["queueClass"])
                c.execute(
                    "UPDATE hb_delivery SET state='claimed',host=%s,claim_id=%s,generation=generation+1,revision=revision+1,updated_at=clock_timestamp() WHERE id=%s",
                    (host, operation, identity),
                )
            c.execute(
                "INSERT INTO hb_claim VALUES(%s,%s,%s,%s)", (a["id"], operation, host, identity)
            )
            receipt = self.claim_receipt(c, identity)
            receipt["admission"] = json_value(observed)
            return receipt

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
                "SELECT d.id FROM hb_delivery d JOIN hb_event e ON e.id=d.event WHERE d.agent=%s AND d.state='pending' AND NOT EXISTS (SELECT 1 FROM hb_work w JOIN hb_goal g ON g.id=w.goal WHERE w.delivery=d.id AND g.state<>'active') ORDER BY e.seq LIMIT 1",
                (a["id"],),
            ).fetchone()
            return json_value({"enabled": placement["enabled"], "busy": busy, "pending": pending})

    @staticmethod
    def claim_receipt(c, identity):
        if identity is None:
            return {"delivery": None}
        d = c.execute("SELECT * FROM hb_delivery WHERE id=%s", (identity,)).fetchone()
        e = c.execute("SELECT * FROM hb_event WHERE id=%s", (d["event"],)).fetchone()
        from .native_bindings import read as read_binding

        binding = read_binding(c, identity, d["generation"])
        from .agent_context import execution
        agent_binding = execution(c, identity, d["generation"])
        from .admission import trace

        return {
            "admissionTrace": json_value(trace(c, identity)),
            "delivery": json_value(d),
            "request": json_value(e),
            **({"nativeGoalBinding": json_value(binding)} if binding else {}),
            **({"agentContextBinding": json_value(agent_binding)} if agent_binding else {}),
        }

    def transition(self, actor_id, identity, operation, payload):
        with self.pool.connection() as c:
            a = self.actor(c, actor_id)
            from .work import acknowledge_cancellation, lock_delivery_goal

            work = lock_delivery_goal(c, identity, a["authority"])
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
            binding = c.execute(
                "SELECT goal,work FROM hb_native_goal_binding WHERE delivery=%s AND generation=%s",
                (identity, d["generation"]),
            ).fetchone()
            goal_id = work["goal"] if work else binding["goal"] if binding else None
            work_id = work["id"] if work else binding["work"] if binding else None
            execution_data = {
                "type": "executionState",
                "deliveryId": identity,
                "replyRecipientId": str(e["author"]) if e["data"].get("expectsReply") else None,
                "state": state,
                "generation": d["generation"],
                "goalId": str(goal_id) if goal_id else None,
                "workId": str(work_id) if work_id else None,
            }
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
                    data=execution_data,
                )
            c.execute(
                "UPDATE hb_delivery SET state=%s,native_thread=COALESCE(%s,native_thread),native_turn=COALESCE(%s,native_turn),result_event=%s,revision=revision+1,updated_at=clock_timestamp() WHERE id=%s",
                (state, payload.get("nativeThread"), payload.get("nativeTurn"), result, identity),
            )
            if state == "cancelled":
                acknowledge_cancellation(c, work)
            if terminal:
                from .work import settle_goal_cancellation

                settle_goal_cancellation(self, c, work)
            if terminal:
                from .native_controls import terminal as settle_controls
                settle_controls(self, c, a, d)
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
            if state == "unknown":
                self.event(
                    c,
                    a,
                    e["room"],
                    "progress",
                    "Execution state unknown; inspect the original native/effect receipts.",
                    audience=e["audience"],
                    recipient=e["author"] if e["audience"] == "direct" else None,
                    source=e["id"],
                    data=execution_data,
                )
                c.execute(
                    "UPDATE hb_attention SET state='unknown',revision=revision+1,updated_at=clock_timestamp() WHERE delivery=%s AND generation=%s AND state IN ('pending','response_submitted','native_submitted')",
                    (identity, d["generation"]),
                )
            reply = None
            if terminal:
                from .communication import completion
                reply = completion(self,c,a,e,d,result,state)
            out = self.claim_receipt(c, identity)
            if reply:
                out["replyDeliveryId"] = reply
            c.execute(
                "INSERT INTO hb_transition VALUES(%s,%s,%s,%s)",
                (identity, operation, fingerprint, Jsonb(out)),
            )
            return out

    def cancel(self, actor_id, identity):
        with self.pool.connection() as c:
            a = self.actor(c, actor_id)
            from .work import acknowledge_cancellation, lock_delivery_goal

            work = lock_delivery_goal(c, identity, a["authority"])
            d = c.execute(
                "SELECT * FROM hb_delivery WHERE id=%s AND authority=%s FOR UPDATE",
                (identity, a["authority"]),
            ).fetchone()
            if not d:
                raise Denied("Delivery unavailable")
            e = self.readable_event(c, a, d["event"])
            if not a["administrator"] and a["id"] != e["author"]:
                raise Denied("Only requester or administrator may cancel")
            if (
                d["state"] in ("pending", "claimed", "running", "unknown")
                and not d["cancellation_requested"]
            ):
                # Claimed work may already be executing: cancellation is a request,
                # never evidence that a shell, model turn or external effect stopped.
                state = (
                    "cancelled"
                    if d["state"] == "pending"
                    else "unknown"
                    if d["state"] == "unknown"
                    else "cancel_requested"
                )
                c.execute(
                    "UPDATE hb_delivery SET state=%s,cancellation_requested=true,revision=revision+1,updated_at=clock_timestamp() WHERE id=%s",
                    (state, identity),
                )
                if state == "cancelled":
                    acknowledge_cancellation(c, work)
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
            from .cooperation import row as cooperation_row
            from .cooperation import view

            goal_id = e["data"].get("goalId")
            if goal_id and cooperation_row(c, goal_id):
                parent = c.execute("SELECT room FROM hb_goal WHERE id=%s", (goal_id,)).fetchone()
                self.room(c, a, parent["room"])
            return json_value(
                {
                    "cooperation": view(c, e["data"].get("goalId"), a["id"])
                    if e["data"].get("goalId")
                    else None,
                    "schema": "lax-agent-context/v1",
                    "authorityId": a["authority"],
                    "agentId": a["id"],
                    "primaryRole": policy["primary_role"],
                    "roleRevision": policy["revision"],
                    "nativeGoalScope": policy["native_goal_scope"],
                    "homeRoomId": policy["home_room"],
                    "rolePurpose": policy["purpose"],
                    "activeRoomId": e["room"],
                    "roomPurpose": room["purpose"],
                    "roomOwnerId": room["owner"],
                    "requestId": e["id"],
                    "goalId": e["data"].get("goalId"),
                    "goalReferenceId": e["data"].get("goalReferenceId"),
                    "workId": e["data"].get("workId"),
                    "attemptId": e["data"].get("attemptId"),
                    "briefingReference": {
                        "schema": "lax-work-briefing/v1",
                        "deliveryId": str(d["id"]),
                        "contentAuthority": "tool-data",
                    },
                    "workAcceptance": e["data"].get("acceptance"),
                    "goalReview": bool(e["data"].get("goalReview")),
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

    def role(self, actor_id, agent, primary, purpose, home_room, revision, native_goal_scope=None, *, command=None):
        from .administrative import mutate
        return mutate(self, actor_id, 'role', [agent, primary, purpose, home_room, revision, native_goal_scope], command)

    def _admin_role(self, c, a, agent, primary, purpose, home_room, revision, native_goal_scope=None):
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
        scope = native_goal_scope or (
            old["native_goal_scope"] if old else "role" if primary == "global" else "work"
        )
        if scope not in ("role", "work") or (primary == "global" and scope != "role"):
            raise ValueError("Global requires an ongoing native responsibility")
        if old and (
            old["native_goal_scope"],
            old["primary_role"],
            old["purpose"],
            str(old["home_room"]) if old["home_room"] else None,
        ) == (scope, primary, purpose, home_room):
            return json_value(old)
        if (old["revision"] if old else 0) != revision:
            raise Conflict("Role revision changed")
        if c.execute(
            """SELECT 1 FROM hb_delivery WHERE agent=%s
               AND state IN ('claimed','running','unknown','cancel_requested') LIMIT 1""",
            (agent,),
        ).fetchone():
            raise Conflict("Responsibility change requires the original execution to settle first; pending work retains its owner")

        c.execute(
            "INSERT INTO hb_agent_role(principal,authority,primary_role,purpose,home_room,native_goal_scope) VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(principal) DO UPDATE SET primary_role=EXCLUDED.primary_role,purpose=EXCLUDED.purpose,home_room=EXCLUDED.home_room,native_goal_scope=EXCLUDED.native_goal_scope,revision=hb_agent_role.revision+1",
            (agent, a["authority"], primary, purpose, home_room, scope),
        )
        return json_value(
            c.execute("SELECT * FROM hb_agent_role WHERE principal=%s", (agent,)).fetchone()
        )
