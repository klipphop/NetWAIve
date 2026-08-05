from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, PositiveInt

from ..models import PendingToolCall, ToolResult


class ResolutionKind(StrEnum):
    SITE = "site"
    MANUFACTURER = "manufacturer"
    DEVICE_TYPE = "device_type"
    MODULE_TYPE = "module_type"
    RACK = "rack"
    DEVICE = "device"
    GENERIC = "generic"


class ResolvedRef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: PositiveInt
    app: str = Field(min_length=1)
    endpoint: str = Field(min_length=1)
    kind: ResolutionKind
    display: str = Field(min_length=1)
    requested: str = Field(min_length=1)
    observed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ResolutionError(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    requested: str = Field(min_length=1)
    candidates: list[ResolvedRef] = Field(default_factory=list)


class IntentResolution(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    request_text: str = Field(min_length=1)
    model: str | None = None
    name: str | None = None
    refs: dict[str, ResolvedRef] = Field(default_factory=dict)
    errors: list[ResolutionError] = Field(default_factory=list)
    explicit_count: int | None = Field(default=None, ge=1, le=512)
    component_templates: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)

    @property
    def resolved(self) -> bool:
        return not self.errors


class RouteKind(StrEnum):
    NDX_IMPORT = "ndx_import"
    CUSTOM_PLAN = "custom_plan"
    CLARIFICATION = "clarification"


class RouteDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: RouteKind
    reason: str = Field(min_length=1)
    ndx_record: dict[str, Any] | None = None


class CertifiedPlanInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    session_id: str = Field(min_length=1)
    generation: str = Field(min_length=1)
    fingerprint: str = Field(min_length=1)
    intent: IntentResolution
    route: RouteDecision


class PendingPlan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    session_id: str = Field(min_length=1)
    generation: str = Field(min_length=1)
    fingerprint: str = Field(min_length=1)
    calls: list[PendingToolCall]


class ExecutionReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    results: list[ToolResult] = Field(default_factory=list)
    failed_step: str | None = None
