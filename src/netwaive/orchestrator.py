from __future__ import annotations

from typing import Any

from .contracts import BatchOperation, ChangePlan
from .mcp_gateway import MCPGateway


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
