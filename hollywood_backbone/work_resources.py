"""Explicit Work resource intent; identity, permission and mutation stay separate."""
from .store import Conflict, Denied, json_value
from .agent_context import owner

CAPABILITY = "canonical-work-resource-routing/v1"
SCHEMA = """
CREATE TABLE IF NOT EXISTS hb_work_resource(
 work uuid PRIMARY KEY REFERENCES hb_work(id), authority uuid NOT NULL REFERENCES hb_authority(id),
 principal uuid NOT NULL REFERENCES hb_principal(id), execution_host uuid NOT NULL REFERENCES hb_host(id),
 context_id text NOT NULL, work_revision bigint NOT NULL CHECK(work_revision>=1), placement_revision bigint NOT NULL CHECK(placement_revision>=1),
 state text NOT NULL CHECK(state IN ('selected','cleared')), revision bigint NOT NULL DEFAULT 1 CHECK(revision>=1),
 workspace uuid REFERENCES hb_physical_workspace(id), workspace_revision bigint, grant_revision bigint,
 CHECK((state='selected' AND workspace IS NOT NULL AND workspace_revision IS NOT NULL AND grant_revision IS NOT NULL)
 OR (state='cleared' AND workspace IS NULL AND workspace_revision IS NULL AND grant_revision IS NULL)));
"""


def snapshot(c, authority, identity):
    work = c.execute("SELECT * FROM hb_work WHERE id=%s AND authority=%s", (identity, authority)).fetchone()
    if not work:
        raise Denied("Work unavailable")
    context = owner(c, work["owner"])
    row = c.execute("SELECT * FROM hb_work_resource WHERE work=%s", (identity,)).fetchone()
    state, reason, resource = "absent", None, None
    host = context["host"] if context else row["execution_host"] if row else None
    if row:
        state = row["state"]
        placed = c.execute("SELECT l.revision FROM hb_placement l JOIN hb_principal p ON p.id=l.agent WHERE l.agent=%s AND l.host=%s AND l.enabled AND p.active",
                           (work["owner"], host)).fetchone() if host else None
        # Placement revisions also track pause/resume. Durable source intent is
        # bound to the same current owner/context/host/work and root/grant below;
        # live admission must be enabled. Native mutation permits keep their own
        # strict lifecycle fences and are never revived by this projection.
        if (not context or str(row["principal"]) != str(work["owner"])
                or row["context_id"] != context["context_id"] or row["execution_host"] != context["host"]
                or row["work_revision"] != work["revision"] or not placed):
            state, reason = "held", "work-participation-changed"
        elif state == "selected":
            root = c.execute("SELECT * FROM hb_physical_workspace WHERE id=%s AND authority=%s",
                             (row["workspace"], authority)).fetchone()
            grant = c.execute("SELECT * FROM hb_physical_agent_grant WHERE workspace=%s AND principal=%s",
                              (row["workspace"], work["owner"])).fetchone()
            if (not root or not grant or root["state"] != "active" or grant["state"] != "active"
                    or not grant["can_read"]
                    or root["host"] != host or grant["execution_host"] != host
                    or root["revision"] != row["workspace_revision"] or grant["revision"] != row["grant_revision"]):
                state, reason = "held", "resource-access-revisions-changed"
            else:
                resource = {"workspace": root, "grant": grant}
    return {"schema": CAPABILITY, "authorityId": authority, "workId": work["id"],
            "workRevision": work["revision"], "agentId": work["owner"], "executionHost": host,
            "selection": row, "selectionRevision": row["revision"] if row else 0,
            "effectiveState": state, "holdReason": reason, "resource": resource, "mayApply": False}


def read(store, actor_id, identity):
    with store.pool.connection() as c:
        actor = store.actor(c, actor_id)
        result = snapshot(c, actor["authority"], identity)
        work = c.execute("SELECT room FROM hb_work WHERE id=%s", (identity,)).fetchone()
        if actor["kind"] == "service":
            from .collaboration import bridge
            if not result["executionHost"]:
                raise Denied("Enrolled Work host unavailable")
            bridge(c, actor, str(result["executionHost"]))
        elif not actor["administrator"] and actor["id"] != result["agentId"]:
            raise Denied("Work owner or administrator required")
        else:
            store.room(c, actor, work["room"])
        return json_value(result)


def select(c, actor, identity, value):
    work = c.execute("SELECT * FROM hb_work WHERE id=%s AND authority=%s FOR UPDATE",
                     (identity, actor["authority"])).fetchone()
    if not work:
        raise Denied("Work unavailable")
    context = owner(c, work["owner"])
    if not context or work["state"] != "assigned" or work["revision"] != value["workRevision"]:
        raise Conflict("Current unfinished Work and owner context required")
    placed = c.execute("SELECT l.revision FROM hb_placement l JOIN hb_principal p ON p.id=l.agent WHERE l.agent=%s AND l.host=%s AND l.enabled AND p.active",
                       (work["owner"], context["host"])).fetchone()
    if not placed:
        raise Denied("Current enabled Work host required")
    bridge = c.execute("""SELECT b.* FROM hb_collaboration_bridge b JOIN hb_host_admission h
        ON h.host=b.host AND h.authority=b.authority AND h.controller=b.controller WHERE b.host=%s AND b.authority=%s AND b.state='active'""",
        (context["host"], actor["authority"])).fetchone()
    if not bridge or CAPABILITY not in bridge["features"]:
        raise Denied("Work routing requires its explicitly enrolled native bridge capability")
    old = c.execute("SELECT * FROM hb_work_resource WHERE work=%s FOR UPDATE", (identity,)).fetchone()
    if (old["revision"] if old else 0) != value["revision"]:
        raise Conflict("Work resource selection revision changed")
    workspace = value["workspaceId"]
    if value["state"] == "selected":
        root = c.execute("SELECT * FROM hb_physical_workspace WHERE id=%s AND authority=%s",
                         (workspace, actor["authority"])).fetchone()
        grant = c.execute("SELECT * FROM hb_physical_agent_grant WHERE workspace=%s AND principal=%s",
                          (workspace, work["owner"])).fetchone()
        if (not root or not grant or root["state"] != "active" or grant["state"] != "active"
                or not grant["can_read"]
                or root["host"] != context["host"] or grant["execution_host"] != context["host"]):
            raise Denied("Explicit authorized local Work resource required")
        if (root["revision"], grant["revision"]) != (value["workspaceRevision"], value["grantRevision"]):
            raise Conflict("Work resource or access revision changed")
    busy = c.execute("""SELECT 1 FROM hb_edit_permit e LEFT JOIN hb_edit_plan p ON p.id=e.plan
        WHERE e.authority=%s AND e.state<>'settled' AND (e.delivery=%s OR p.integrator_work=%s
        OR EXISTS(SELECT 1 FROM hb_edit_consent v WHERE v.plan=e.plan AND v.work=%s)) LIMIT 1""",
        (actor["authority"], work["delivery"], work["id"], work["id"])).fetchone()
    claims = c.execute("""SELECT 1 FROM hb_path_claim WHERE work=%s AND state<>'released'
        AND (%s::uuid IS NULL OR workspace_id<>%s::uuid) LIMIT 1""", (identity, workspace, workspace)).fetchone()
    consents = c.execute("""SELECT 1 FROM hb_edit_consent v JOIN hb_edit_plan p ON p.id=v.plan
        WHERE v.work=%s AND v.active AND p.state IN ('collecting','ready')
        AND (%s::uuid IS NULL OR v.workspace_id<>%s::uuid) LIMIT 1""", (identity, workspace, workspace)).fetchone()
    if busy or claims or consents:
        raise Conflict("Settle original mutations or ownership before rerouting Work")
    c.execute("""INSERT INTO hb_work_resource(work,authority,principal,execution_host,context_id,work_revision,
        placement_revision,state,workspace,workspace_revision,grant_revision) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT(work) DO UPDATE SET principal=EXCLUDED.principal,execution_host=EXCLUDED.execution_host,
        context_id=EXCLUDED.context_id,work_revision=EXCLUDED.work_revision,placement_revision=EXCLUDED.placement_revision,
        state=EXCLUDED.state,workspace=EXCLUDED.workspace,workspace_revision=EXCLUDED.workspace_revision,
        grant_revision=EXCLUDED.grant_revision,revision=hb_work_resource.revision+1""",
        (identity,actor["authority"],work["owner"],context["host"],context["context_id"],work["revision"],
         placed["revision"],value["state"],workspace,value["workspaceRevision"],value["grantRevision"]))
    return json_value(snapshot(c, actor["authority"], identity))


def check_target(c, authority, identity, target):
    value = snapshot(c, authority, identity)
    if value["effectiveState"] == "absent":
        return
    if value["effectiveState"] != "selected" or str(value["selection"]["workspace"]) != str(target["workspaceId"]):
        raise Conflict("Current Work resource intent differs from captured physical target")

