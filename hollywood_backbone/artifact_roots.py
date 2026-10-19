"""Artifact locations are physical resources, separate from agent context identity."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .store import Conflict, Denied


class ArtifactRoot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_: Literal["hollywood-artifact-root/v1"] = Field(alias="schema")
    workspaceId: UUID
    workspaceRevision: int = Field(ge=1, strict=True)
    grantRevision: int = Field(ge=1, strict=True)
    environmentSha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    rootIdentitySha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def normalize(evidence):
    """Keep an older accepted request's canonical bytes unchanged when absent."""
    for entry in evidence:
        if entry.get("artifactRoot") is None:
            entry.pop("artifactRoot", None)


def validate(c, actor, delivery, evidence):
    """Reauthorize every explicit physical root in the report transaction.

    The host connector hashes descriptor-rooted files. Hollywood verifies the
    current authority, host, physical registry and read grant. This is neither
    a shared-filesystem assumption nor independent acceptance of the deliverable.
    """
    roots = [e for e in evidence if e.get("artifactRoot") is not None]
    if not roots:
        return
    from .agent_context import execution

    binding = execution(c, delivery["id"], delivery["generation"])
    begin=c.execute("""SELECT result FROM hb_context_operation WHERE agent=%s
        AND purpose='writer' AND operation=%s""",
        (actor["id"],"context-begin:"+str(delivery["native_turn"]))).fetchone()
    scopes=[s for s in begin["result"].get("executionScopes",[]) if
        s["deliveryId"]==str(delivery["id"]) and s["generation"]==delivery["generation"]] if begin else []
    if scopes:
        if len(scopes)!=1:
            raise Denied("One original artifact execution scope required")
        scope=scopes[0]
        from .purpose_effects import binding as purpose_binding
        source={**scope,"executionScopeId":scope["id"],"requestId":scope["sourceRequestId"]}
        binding=purpose_binding(c,actor,source)
    if not binding or binding["principal"] != actor["id"]:
        raise Denied("Captured persistent artifact reporter required")
    for entry in roots:
        if (entry["hostId"] != str(delivery["host"])
                or entry["workspaceId"] != str(binding["workspace_id"])):
            raise Denied("Artifact context or execution host changed")
        target = entry["artifactRoot"]
        root = c.execute(
            "SELECT * FROM hb_physical_workspace WHERE id=%s AND authority=%s FOR SHARE",
            (target["workspaceId"], actor["authority"]),
        ).fetchone()
        access = c.execute(
            "SELECT * FROM hb_physical_agent_grant WHERE workspace=%s AND principal=%s FOR SHARE",
            (target["workspaceId"], actor["id"]),
        ).fetchone()
        if not root or not access:
            raise Denied("Current authorized artifact resource required")
        if (root["revision"] != target["workspaceRevision"]
                or access["revision"] != target["grantRevision"]
                or root["environment_sha256"] != target["environmentSha256"]
                or root["root_identity_sha256"] != target["rootIdentitySha256"]):
            raise Conflict("Artifact resource, environment or grant changed")
        if (root["state"] != "active" or access["state"] != "active"
                or not access["can_read"]
                or str(root["host"]) != str(delivery["host"])
                or str(access["execution_host"]) != str(delivery["host"])):
            raise Denied("Artifact resource is unavailable on this execution host")
