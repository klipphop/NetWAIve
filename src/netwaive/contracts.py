from __future__ import annotations

from typing import Any, Literal

from urllib.parse import unquote

from pydantic import BaseModel, ConfigDict, Field, field_validator


from .selection import SelectionResult


class BatchOperation(BaseModel):
    """One normalized NetBox REST mutation."""

    model_config = ConfigDict(extra="forbid")

    method: Literal["POST", "PATCH", "DELETE"]
    endpoint: str = Field(min_length=1)
    data: dict[str, Any]

    @field_validator("endpoint")
    @classmethod
    def normalize_endpoint(cls, value: str) -> str:
        value = value.strip()
        decoded = unquote(value)
        if not value or decoded.lower().startswith(("http://", "https://")):
            raise ValueError("endpoint must be a relative NetBox API path")
        value = "/" + value.strip("/") + "/"
        if value.startswith("/api/"):
            value = value[4:]
        decoded_path = unquote(value).lower()
        if value == "/" or ".." in decoded_path or "?" in value or "#" in value or "%" in value:
            raise ValueError("endpoint contains an unsafe path")
        if decoded_path.startswith(("/users/", "/admin/", "/auth/")):
            raise ValueError("endpoint is outside the NetBox infrastructure scope")
        return value


class ChangePlan(BaseModel):
    """Complete human-reviewable plan, independent of the LLM/tool format."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1)
    operations: list[BatchOperation] = Field(min_length=1)
    affected_objects: list[dict[str, Any]] = Field(default_factory=list)
    dependencies: list[dict[str, Any]] = Field(default_factory=list)
    selection: SelectionResult | None = None
    risk: Literal["low", "medium", "high"] = "medium"

    def mcp_arguments(self) -> dict[str, Any]:
        payload = {"operations": [item.model_dump() for item in self.operations]}
        if self.selection is not None:
            payload["selection"] = self.selection.model_dump()
        return payload

    @property
    def count(self) -> int:
        return len(self.operations)


class ToolObservation(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: Any = None


class CopilotResponse(BaseModel):
    message: str
    observations: list[ToolObservation] = Field(default_factory=list)
    change_plan: ChangePlan | None = None
    executed: bool = False
