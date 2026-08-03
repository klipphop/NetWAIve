from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator


NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
NDX_COMPONENT_ENDPOINTS = {
    "interfaces": "interface-templates",
    "power-ports": "power-port-templates",
    "console-ports": "console-port-templates",
    "module-bays": "module-bay-templates",
}


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


class NDXDeviceTypeDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: NonEmptyStr
    slug: NonEmptyStr
    u_height: float = Field(default=1, gt=0)
    front_image: Any = None
    rear_image: Any = None


class NDXImportPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manufacturer: NonEmptyStr
    device_type: NDXDeviceTypeDTO
    component_templates: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)

    @field_validator("component_templates")
    @classmethod
    def validate_components(cls, value: dict[str, list[dict[str, Any]]]):
        unknown = set(value) - set(NDX_COMPONENT_ENDPOINTS)
        if unknown:
            raise ValueError(f"collections NDX inconnues: {sorted(unknown)}")
        for components in value.values():
            for component in components:
                name = component.get("name") if isinstance(component, dict) else None
                if not isinstance(name, str) or not name.strip():
                    raise ValueError("un template NDX doit avoir un nom non vide")
        return value

    def interface_count(self) -> int:
        return len(self.component_templates.get("interfaces", []))


class PendingToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any]


class AgentResponse(BaseModel):
    message: str
    pending_confirmation: list[PendingToolCall] = Field(default_factory=list)
    tool_results: list[ToolResult] = Field(default_factory=list)
