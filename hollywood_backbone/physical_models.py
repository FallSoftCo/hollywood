"""Protected physical-resource authority, distinct from private execution identity."""
from typing import Literal
from uuid import UUID
from pydantic import Field
from .collaboration_models_base import Input

SHA = r"^[a-f0-9]{64}$"
CAPABILITY = "canonical-edit-plans/v2"


class WorkspaceConfiguration(Input):
    operationId: str = Field(min_length=1, max_length=128)
    revision: int = Field(ge=0, strict=True)
    state: Literal["active", "revoked"]
    rootPath: str = Field(min_length=2, max_length=2048)
    environmentSha256: str = Field(pattern=SHA)
    rootIdentitySha256: str = Field(pattern=SHA)
    registrationSha256: str = Field(pattern=SHA)


class WorkspaceGrant(Input):
    operationId: str = Field(min_length=1, max_length=128)
    revision: int = Field(ge=0, strict=True)
    workspaceRevision: int = Field(ge=1, strict=True)
    workRevision: int = Field(ge=1, strict=True)
    state: Literal["active", "revoked"]


class ResolvedPath(Input):
    relativePath: str = Field(min_length=1, max_length=1024)
    resolvedPath: str = Field(min_length=2, max_length=3072)


class PhysicalTarget(Input):
    schema_: Literal["hollywood-physical-target/v1"] = Field(alias="schema")
    workspaceId: UUID
    workspaceRevision: int = Field(ge=1, strict=True)
    grantRevision: int = Field(ge=1, strict=True)
    environmentSha256: str = Field(pattern=SHA)
    rootIdentitySha256: str = Field(pattern=SHA)
    resolution: Literal["beneath-no-symlinks/v1"]
    actionDigest: str = Field(pattern=SHA)
    paths: list[ResolvedPath] = Field(max_length=32)
