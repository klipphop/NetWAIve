from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator


NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
NDX_OBJECT_CONFIG = {
    "device-type": {
        "endpoint": "device-types",
        "relation": "device_type",
        "directory": "device-types",
        "label": "DeviceType",
        "requires_interfaces": True,
    },
    "module-type": {
        "endpoint": "module-types",
        "relation": "module_type",
        "directory": "module-types",
        "label": "ModuleType",
        "requires_interfaces": False,
    },
}
NDX_COMPONENT_ENDPOINTS = {
    "interfaces": "interface-templates",
    "power-ports": "power-port-templates",
    "console-ports": "console-port-templates",
    "console-server-ports": "console-server-port-templates",
    "power-outlets": "power-outlet-templates",
    "front-ports": "front-port-templates",
    "rear-ports": "rear-port-templates",
    "module-bays": "module-bay-templates",
    "device-bays": "device-bay-templates",
    "inventory-items": "inventory-item-templates",
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


class NDXParentDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: NonEmptyStr
    slug: NonEmptyStr | None = None
    part_number: str = ""
    u_height: float | None = Field(default=None, gt=0)
    airflow: str | None = None
    weight: float | None = None
    weight_unit: str | None = None
    description: str | None = None
    comments: str | None = None
    attributes: dict[str, Any] | None = None
    profile: str | None = None
    exclude_from_utilization: bool | None = None
    is_full_depth: bool | None = None
    subdevice_role: str | None = None
    front_image: Any = None
    rear_image: Any = None


class NDXImportPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    object_type: Literal["device-type", "module-type"]
    manufacturer: NonEmptyStr
    parent: NDXParentDTO
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

    def component_count(self) -> int:
        return sum(len(items) for items in self.component_templates.values())


class PendingToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any]


class AgentResponse(BaseModel):
    message: str
    pending_confirmation: list[PendingToolCall] = Field(default_factory=list)
    tool_results: list[ToolResult] = Field(default_factory=list)
