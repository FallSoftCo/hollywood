"""Revisioned goal definition and strategy inputs; verification methods are not results."""
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, model_validator

class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")

class Criterion(Input):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    requirement: str = Field(min_length=1, max_length=1000)
    method: Literal["command", "artifact", "review"]
    verification: str = Field(min_length=1, max_length=1000)

class Definition(Input):
    objective: str = Field(min_length=1, max_length=6000)
    criteria: list[Criterion] = Field(min_length=1, max_length=32)
    assumptions: list[str] = Field(max_length=16)
    openQuestions: list[str] = Field(max_length=16)
    @model_validator(mode="after")
    def bounded(self):
        import json
        if len({c.id for c in self.criteria}) != len(self.criteria):
            raise ValueError("Unique criterion IDs required")
        if any(not 1 <= len(s) <= 1000 for s in self.assumptions+self.openQuestions):
            raise ValueError("Bounded assumptions/questions required")
        if len(json.dumps(self.model_dump()).encode()) > 12000:
            raise ValueError("Definition exceeds12000 bytes; retain references to larger artifacts")
        return self

class DefinitionWrite(Input):
    operationId: str = Field(min_length=1, max_length=128)
    revision: int = Field(ge=1)
    definitionRevision: int = Field(ge=0)
    sourceEventId: UUID
    definition: Definition
    note: str = Field(min_length=1, max_length=1000)

class DefinitionAdopt(Input):
    operationId: str = Field(min_length=1, max_length=128)
    revision: int = Field(ge=1)
    definitionRevision: int = Field(ge=1)
    definitionSha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    sourceEventId: UUID
    note: str = Field(min_length=1, max_length=1000)

class DefinitionResolve(DefinitionAdopt):
    disposition: Literal["rejected", "deferred"]

class Alternative(Input):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    approach: str = Field(min_length=1, max_length=1500)
    tradeoffs: str = Field(min_length=1, max_length=1500)

class Strategy(Input):
    alternatives: list[Alternative] = Field(min_length=1, max_length=8)
    selected: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    rationale: str = Field(min_length=1, max_length=2000)
    frontierWorkIds: list[UUID] = Field(max_length=32)
    replanWhen: list[str] = Field(max_length=16)
    @model_validator(mode="after")
    def bounded(self):
        import json
        ids=[a.id for a in self.alternatives]
        if len(set(ids))!=len(ids) or self.selected not in ids:
            raise ValueError("One selected declared alternative required")
        if len(set(self.frontierWorkIds))!=len(self.frontierWorkIds):
            raise ValueError("Duplicate frontier Work")
        if any(not 1<=len(s)<=1000 for s in self.replanWhen):
            raise ValueError("Bounded replanning conditions required")
        if len(json.dumps(self.model_dump(mode="json")).encode())>12000:
            raise ValueError("Strategy exceeds12000 bytes")
        return self

class StrategyWrite(Input):
    operationId: str = Field(min_length=1, max_length=128)
    revision: int = Field(ge=1)
    planRevision: int = Field(ge=0)
    definitionRevision: int = Field(ge=1)
    strategy: Strategy
    sourceEventId: UUID
    note: str = Field(min_length=1, max_length=1000)
