from __future__ import annotations

from typing import Any

from .contracts import BatchOperation, ChangePlan
from .mcp_gateway import MCPGateway
from .selection import SelectionResult
from .selection_engine import SelectionEngine


class CopilotOrchestrator:
    """MCP-first orchestration boundary; LLM planning stays above this layer."""

    def __init__(self, mcp: MCPGateway):
        self.mcp = mcp

    def inspect(self, endpoint: str, *, object_id: int | None = None, depth: int = 2, params: dict[str, Any] | None = None) -> Any:
        return self.mcp.call("netbox_inspect_tree", {
            "endpoint": endpoint,
            "object_id": object_id,
            "depth": depth,
            "params": params or {},
        })

    def select(
        self,
        payload: Any,
        *,
        endpoint: str,
        key_field: str = "id",
        label_fields: tuple[str, ...] = (),
        exclude_keys: dict[str, str] | None = None,
        duplicate_keys: set[str] | None = None,
    ) -> SelectionResult:
        """Convert a live MCP collection into a canonical selection."""
        return SelectionEngine().build(
            payload,
            endpoint=endpoint,
            key_field=key_field,
            label_fields=label_fields,
            exclude_keys=exclude_keys,
            duplicate_keys=duplicate_keys,
        )

    @staticmethod
    def attach_selection(plan: ChangePlan, selection: SelectionResult) -> ChangePlan:
        """Bind the audited selection to the immutable plan payload."""
        selection.validate_consistency()
        if not selection.selected:
            raise ValueError("cannot attach an empty selection to a ChangePlan")
        return ChangePlan.model_validate({**plan.model_dump(), "selection": selection.model_dump()})
    def validate(self, plan: ChangePlan) -> ChangePlan:
        """Validate all operations before the first remote mutation."""
        for operation in plan.operations:
            if operation.method in {"PATCH", "DELETE"}:
                parts = operation.endpoint.strip("/").split("/")
                if not parts[-1].isdigit():
                    raise ValueError(f"{operation.method} endpoint must end in a numeric object id: {operation.endpoint}")
        return ChangePlan.model_validate(plan.model_dump())

    def execute(self, plan: ChangePlan) -> Any:
        validated = self.validate(plan)
        return self.mcp.call("netbox_batch_execute", validated.mcp_arguments())
