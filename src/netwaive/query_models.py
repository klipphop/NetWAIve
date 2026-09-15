from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: str
    endpoint: str
    filters: dict[str, Any] = Field(default_factory=dict)
    pages: int = 0
    object_count: int = 0
    read_only: bool = True


class QueryResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["collection", "comparison", "object", "graph"]
    title: str
    items: list[dict[str, Any]] = Field(default_factory=list)
    total: int = 0
    evidence: list[Evidence] = Field(default_factory=list)
    relation: dict[str, Any] | None = None
    complete: bool = True

    def assert_consistent(self) -> "QueryResult":
        if self.total != len(self.items):
            raise ValueError(f"query total mismatch: total={self.total}, items={len(self.items)}")
        if not self.evidence or not all(item.read_only for item in self.evidence):
            raise ValueError("query evidence must be read-only and non-empty")
        return self
