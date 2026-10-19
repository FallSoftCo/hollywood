"""Persistent agent identity/context, separately fenced execution and communication."""

from .store import Conflict, Denied, digest, json_value

CAPABILITY = "persistent-agent-context/v1"


def execution(c, delivery, generation):
    return c.execute(
        "SELECT * FROM hb_agent_execution WHERE delivery=%s AND generation=%s",
        (delivery, generation),
    ).fetchone()



def owner(c, agent):
    """Current metadata identity; a purpose enrollment never falls back to legacy."""
    system = c.execute("SELECT * FROM hb_context_system WHERE agent=%s", (agent,)).fetchone()
    if system:
        writer = c.execute("SELECT context_id,native_thread FROM hb_purpose_context WHERE agent=%s AND purpose='writer'",
                           (agent,)).fetchone()
        if not system["enabled"] or not writer:
            return None
        return {"principal": system["agent"], "authority": system["authority"],
                "context_id": str(writer["context_id"]), "native_thread": writer["native_thread"],
                "host": system["host"], "workspace_id": system["workspace"],
                "role_revision": system["role_revision"], "bindingKind": "purpose"}
    legacy = c.execute("SELECT * FROM hb_agent_context WHERE principal=%s", (agent,)).fetchone()
    return {**legacy, "bindingKind": "legacy"} if legacy else None

def read(store, actor_id, agent):
    with store.pool.connection() as c:
        a = store.actor(c, actor_id)
        if str(a["id"]) != agent and not a["administrator"]:
            raise Denied("Agent context is private to its owner or administrator")
        target = c.execute(
            "SELECT id FROM hb_principal WHERE id=%s AND authority=%s AND kind='agent'",
            (agent, a["authority"]),
        ).fetchone()
        if not target:
            raise Denied("Agent unavailable")
        row = c.execute(
            "SELECT * FROM hb_agent_context WHERE principal=%s", (agent,)
        ).fetchone()
        return json_value({"schema": CAPABILITY, "context": row})


def bind(store, actor_id, delivery, payload):
    with store.pool.connection() as c:
        a = store.actor(c, actor_id)
        if a["kind"] != "agent":
            raise Denied("Execution agent required")
        # Match the agent claim lock. One persistent context admits one execution.
        c.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (str(a["id"]),)
        )
        d = c.execute(
            "SELECT * FROM hb_delivery WHERE id=%s AND agent=%s FOR UPDATE",
            (delivery, a["id"]),
        ).fetchone()
        if (
            not d
            or d["claim_id"] != payload["claimId"]
            or d["generation"] != payload["generation"]
        ):
            raise Conflict("Original execution claim changed")
        event = store.readable_event(c, a, d["event"])
        store.room(c, a, event["room"], "can_post")
        role = c.execute(
            "SELECT revision FROM hb_agent_role WHERE principal=%s FOR SHARE",
            (a["id"],),
        ).fetchone()
        if not role or role["revision"] != payload["roleRevision"]:
            raise Conflict("Captured responsibility changed")
        old = execution(c, delivery, d["generation"])
        key = digest(payload)
        if old:
            if old["digest"] != key:
                raise Conflict("Original agent execution binding changed")
            return json_value(old)
        if d["state"] != "claimed" or d["cancellation_requested"]:
            raise Conflict("Execution no longer admits a context binding")
        if d["native_thread"] and d["native_thread"] != payload["nativeThread"]:
            raise Conflict("Captured native thread changed")
        c.execute(
            """INSERT INTO hb_agent_context
               (principal,authority,context_id,native_thread,host,workspace_id)
               VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(principal) DO NOTHING""",
            (
                a["id"],
                a["authority"],
                payload["contextId"],
                payload["nativeThread"],
                d["host"],
                payload["workspaceId"],
            ),
        )
        owner = c.execute(
            "SELECT * FROM hb_agent_context WHERE principal=%s FOR SHARE", (a["id"],)
        ).fetchone()
        expected = (
            payload["contextId"],
            payload["nativeThread"],
            str(d["host"]),
            payload["workspaceId"],
        )
        if (
            owner["context_id"],
            owner["native_thread"],
            str(owner["host"]),
            str(owner["workspace_id"]),
        ) != expected:
            raise Conflict(
                "Persistent context differs; explicit context/host transfer required"
            )
        c.execute(
            """INSERT INTO hb_agent_execution
               (delivery,generation,principal,context_id,native_thread,workspace_id,role_revision,digest)
               VALUES(%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                delivery,
                d["generation"],
                a["id"],
                payload["contextId"],
                payload["nativeThread"],
                payload["workspaceId"],
                payload["roleRevision"],
                key,
            ),
        )
        c.execute(
            "UPDATE hb_delivery SET native_thread=%s WHERE id=%s",
            (payload["nativeThread"], delivery),
        )
        return json_value(execution(c, delivery, d["generation"]))


def grant(store, actor_id, agent, credential_digest, allow_requests, *, command=None):
    from .administrative import mutate

    return mutate(
        store,
        actor_id,
        "communication",
        [agent, credential_digest, allow_requests],
        command,
    )


def admin_grant(store, c, actor, agent, credential_digest, allow_requests):
    target = c.execute(
        """SELECT p.id FROM hb_principal p JOIN hb_token t ON t.principal=p.id
           WHERE p.id=%s AND p.authority=%s AND p.kind='agent'
             AND p.active AND t.digest=%s AND NOT t.revoked""",
        (agent, actor["authority"], credential_digest),
    ).fetchone()
    if not target:
        raise Denied("Enrolled agent credential required")
    if c.execute("SELECT 1 FROM hb_placement WHERE agent=%s", (agent,)).fetchone():
        raise Denied(
            "A placed native agent cannot enroll its execution credential as an external transport"
        )
    c.execute(
        """INSERT INTO hb_agent_communication VALUES(%s,%s,%s)
           ON CONFLICT(token_digest) DO UPDATE SET allow_requests=EXCLUDED.allow_requests""",
        (credential_digest, agent, allow_requests),
    )
    return {
        "agentId": agent,
        "credentialDigest": credential_digest,
        "allowRequests": allow_requests,
        "scope": "room membership still required; no execution or administrator authority granted",
    }


def communication_allowed(c, request, principal):
    # Explicitly enrolled external transport only. Native worker credentials
    # receive no such grant and cannot bypass original-execution fencing.
    row = c.execute(
        """SELECT allow_requests FROM hb_agent_communication
           WHERE token_digest=%s AND principal=%s""",
        (request["tokenDigest"], principal["id"]),
    ).fetchone()
    return bool(
        row
        and request["action"] == "post"
        and (
            request["body"].get("kind") == "message"
            or row["allow_requests"]
            and request["body"].get("kind") == "request"
        )
    )
