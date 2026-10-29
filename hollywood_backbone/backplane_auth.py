"""Transport identity enrollment for single-context agent communication.

NATS authenticates the host's source subject. The canonical authority separately
binds that host to an agent's existing placement and credential scope. Human
identities are never admitted through this machine-to-agent path. Enrollment is
an explicit administrator operation, not a message-provided credential.
"""
from dataclasses import dataclass
from uuid import UUID

from .store import Conflict, Denied


SCHEMA = """
CREATE TABLE IF NOT EXISTS hb_backplane_agent_binding (
 authority uuid NOT NULL,
 host uuid NOT NULL,
 principal uuid NOT NULL,
 token_digest text NOT NULL REFERENCES hb_token(digest),
 placement_generation bigint NOT NULL,
 revision bigint NOT NULL DEFAULT 1,
 enabled boolean NOT NULL DEFAULT true,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 created_by uuid NOT NULL,
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_by uuid NOT NULL,
 PRIMARY KEY(authority,host,principal),
 FOREIGN KEY(authority,host) REFERENCES hb_host(authority,id),
 FOREIGN KEY(authority,principal) REFERENCES hb_principal(authority,id),
 FOREIGN KEY(authority,created_by) REFERENCES hb_principal(authority,id),
 FOREIGN KEY(authority,updated_by) REFERENCES hb_principal(authority,id)
);
"""


@dataclass(frozen=True)
class BoundAgent:
    authority_id: str
    host_id: str
    principal_id: str
    token_digest: str
    placement_generation: int
    binding_revision: int


class AgentBindings:
    def __init__(self, store):
        self.store = store

    def migrate(self):
        # Explicit installer/qualification call only. Constructing a transport
        # or receiving a message never changes schema or creates enrollment.
        with self.store.pool.connection() as connection:
            connection.execute(SCHEMA)

    @staticmethod
    def _legacy_credential(connection, principal, token_digest):
        credential = connection.execute(
            "SELECT digest FROM hb_token WHERE principal=%s AND digest=%s AND NOT revoked FOR SHARE",
            (principal, token_digest),
        ).fetchone()
        if not credential:
            raise Denied("Agent credential is inactive")
        # Purpose credentials have additional HTTP-only admission restrictions.
        # They cannot acquire general single-context authority through NATS.
        if connection.execute(
            "SELECT 1 FROM hb_purpose_context WHERE agent=%s OR token_digest=%s LIMIT 1",
            (principal, token_digest),
        ).fetchone():
            raise Denied("Purpose contexts are not single-context enrollment")

    def bind(self, administrator, *, host_id, principal_id, token_digest,
             expected_revision=None):
        host_id, principal_id = str(UUID(host_id)), str(UUID(principal_id))
        with self.store.pool.connection() as connection:
            actor = self.store.actor(connection, administrator)
            if not actor["administrator"]:
                raise Denied("Administrator enrollment required")
            placement = connection.execute(
                """SELECT p.id,p.authority,p.kind,p.active,pl.generation,pl.enabled
                   FROM hb_principal p JOIN hb_placement pl ON pl.agent=p.id
                   WHERE p.id=%s AND p.authority=%s AND pl.authority=p.authority
                   AND pl.host=%s FOR SHARE OF p,pl""",
                (principal_id, actor["authority"], host_id),
            ).fetchone()
            if not placement or placement["kind"] != "agent" or not placement["active"] or not placement["enabled"]:
                raise Denied("Active agent placement on enrolled host required")
            self._legacy_credential(connection, principal_id, token_digest)
            old = connection.execute(
                """SELECT * FROM hb_backplane_agent_binding
                   WHERE authority=%s AND host=%s AND principal=%s FOR UPDATE""",
                (actor["authority"], host_id, principal_id),
            ).fetchone()
            if old:
                if (old["token_digest"] == token_digest
                        and old["placement_generation"] == placement["generation"]
                        and old["enabled"]):
                    return old["revision"]
                if expected_revision != old["revision"]:
                    raise Conflict("Binding revision changed; explicit replacement required")
                row = connection.execute(
                    """UPDATE hb_backplane_agent_binding SET token_digest=%s,
                       placement_generation=%s,enabled=true,revision=revision+1,
                       updated_at=clock_timestamp(),updated_by=%s
                       WHERE authority=%s AND host=%s AND principal=%s RETURNING revision""",
                    (token_digest, placement["generation"], actor["id"], actor["authority"], host_id, principal_id),
                ).fetchone()
                return row["revision"]
            if expected_revision is not None:
                raise Conflict("Binding does not exist")
            inserted = connection.execute(
                """INSERT INTO hb_backplane_agent_binding
                   (authority,host,principal,token_digest,placement_generation,created_by,updated_by)
                   VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING revision""",
                (actor["authority"], host_id, principal_id, token_digest,
                 placement["generation"], actor["id"], actor["id"]),
            ).fetchone()
            if inserted:
                return inserted["revision"]
            raced = connection.execute(
                """SELECT * FROM hb_backplane_agent_binding
                   WHERE authority=%s AND host=%s AND principal=%s FOR UPDATE""",
                (actor["authority"], host_id, principal_id),
            ).fetchone()
            if (raced and raced["enabled"] and raced["token_digest"] == token_digest
                    and raced["placement_generation"] == placement["generation"]):
                return raced["revision"]
            raise Conflict("Concurrent binding enrollment changed")

    def resolve(self, *, authority_id, authenticated_host_id, principal_id):
        with self.store.pool.connection() as connection:
            return self.resolve_in_transaction(connection, authority_id=authority_id,
                authenticated_host_id=authenticated_host_id, principal_id=principal_id)

    @staticmethod
    def resolve_in_transaction(connection, *, authority_id, authenticated_host_id, principal_id):
        authority_id, host_id, principal_id = map(str, map(UUID,
            (authority_id, authenticated_host_id, principal_id)))
        row = connection.execute(
            """SELECT b.* FROM hb_backplane_agent_binding b
               JOIN hb_principal p ON p.id=b.principal AND p.authority=b.authority
               JOIN hb_placement pl ON pl.agent=p.id AND pl.authority=b.authority
               AND pl.host=b.host AND pl.generation=b.placement_generation
               JOIN hb_token t ON t.digest=b.token_digest AND t.principal=p.id
               WHERE b.authority=%s AND b.host=%s AND b.principal=%s
               AND b.enabled AND p.active AND p.kind='agent'
               AND pl.enabled AND NOT t.revoked FOR SHARE OF b,p,pl,t""",
            (authority_id, host_id, principal_id),
        ).fetchone()
        if not row:
            raise Denied("No current host-agent transport binding")
        AgentBindings._legacy_credential(connection, principal_id, row["token_digest"])
        return BoundAgent(authority_id, host_id, principal_id,
                          row["token_digest"], row["placement_generation"], row["revision"])

    def revoke(self, administrator, *, host_id, principal_id, expected_revision):
        with self.store.pool.connection() as connection:
            actor = self.store.actor(connection, administrator)
            if not actor["administrator"]:
                raise Denied("Administrator revocation required")
            row = connection.execute(
                """SELECT revision,enabled FROM hb_backplane_agent_binding
                   WHERE authority=%s AND host=%s AND principal=%s FOR UPDATE""",
                (actor["authority"], str(UUID(host_id)), str(UUID(principal_id))),
            ).fetchone()
            if not row:
                raise Denied("Binding unavailable")
            if not row["enabled"]:
                return row["revision"]
            if row["revision"] != expected_revision:
                raise Conflict("Binding revision changed")
            return connection.execute(
                """UPDATE hb_backplane_agent_binding SET enabled=false,
                   revision=revision+1,updated_at=clock_timestamp(),updated_by=%s
                   WHERE authority=%s AND host=%s AND principal=%s RETURNING revision""",
                (actor["id"], actor["authority"], host_id, principal_id),
            ).fetchone()["revision"]


def validate_admission(connection, admission, actor_id, token_digest):
    """Recheck under the existing domain transaction/fence, not just preflight."""
    if (not isinstance(admission, BoundAgent) or admission.principal_id != str(actor_id)
            or admission.token_digest != token_digest):
        raise Denied("Invalid backplane admission context")
    current = AgentBindings.resolve_in_transaction(connection,
        authority_id=admission.authority_id,
        authenticated_host_id=admission.host_id, principal_id=admission.principal_id)
    if current != admission:
        raise Denied("Backplane placement or enrollment changed")
