from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class NetBoxReadArgs(BaseModel):
    """Lecture universelle d'un endpoint NetBox."""

    model_config = ConfigDict(extra="allow")

    app: str = Field(min_length=1, max_length=100)
    endpoint: str = Field(min_length=1, max_length=200)
    method: Literal["filter", "all", "get", "count"] = "filter"
    kwargs: dict[str, Any] = Field(default_factory=dict)
    limit: int = Field(default=50, ge=1, le=200)

    def merged_kwargs(self) -> dict[str, Any]:
        merged = dict(self.kwargs)
        merged.update(self.model_extra or {})
        return merged


class NetBoxWriteArgs(BaseModel):
    """Écriture universelle sur un endpoint NetBox."""

    app: str = Field(min_length=1, max_length=100)
    endpoint: str = Field(min_length=1, max_length=200)
    action: Literal["create", "update", "delete"]
    data: dict[str, Any]


class GetEndpointSchemaArgs(BaseModel):
    """Découverte OpenAPI universelle d'un endpoint NetBox."""

    app: str = Field(min_length=1, max_length=100)
    endpoint: str = Field(min_length=1, max_length=200)


class ToolResult(BaseModel):
    ok: bool
    message: str
    data: Any = None


class PendingToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any]


class AgentResponse(BaseModel):
    message: str
    pending_confirmation: list[PendingToolCall] = Field(default_factory=list)
    tool_results: list[ToolResult] = Field(default_factory=list)
