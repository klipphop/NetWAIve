from __future__ import annotations

import re
from typing import Any, Protocol

from ..models import ToolResult
from .contracts import ExecutionReport, PendingPlan


class StrictWritePort(Protocol):
    def execute(self, name: str, arguments: dict[str, Any]) -> ToolResult: ...
    def import_ndx_object(self, arguments: dict[str, Any]) -> ToolResult: ...


class ExecutionEngine:
    """Exécute exactement un plan certifié; aucun lookup ni normalisation."""

    def __init__(self, port: StrictWritePort):
        self.port = port

    def execute(self, plan: PendingPlan, *, session_id: str, generation: str, fingerprint: str) -> ExecutionReport:
        if (plan.session_id, plan.generation, plan.fingerprint) != (session_id, generation, fingerprint):
            return ExecutionReport(ok=False, results=[ToolResult(ok=False, message="Plan périmé ou issu d’une autre session.")])
        results: list[ToolResult] = []
        outputs: dict[str, Any] = {}
        for call in plan.calls:
            try:
                arguments = self._resolve_certified_refs(call.arguments, outputs)
            except ValueError as exc:
                return ExecutionReport(ok=False, results=[ToolResult(ok=False, message=str(exc))], failed_step=call.id)
            result = self.port.import_ndx_object(arguments) if call.name == "import_ndx_object" else self.port.execute(call.name, arguments)
            results.append(result)
            outputs[call.id] = result.data
            if not result.ok:
                return ExecutionReport(ok=False, results=results, failed_step=call.id)
        return ExecutionReport(ok=True, results=results)

    @classmethod
    def _resolve_certified_refs(cls, value: Any, outputs: dict[str, Any]) -> Any:
        if isinstance(value, dict):
            return {key: cls._resolve_certified_refs(item, outputs) for key, item in value.items()}
        if isinstance(value, list):
            return [cls._resolve_certified_refs(item, outputs) for item in value]
        if not isinstance(value, str) or "${" not in value:
            return value
        match = re.fullmatch(r"\$\{([A-Za-z0-9_-]+)\.data\.id\}", value)
        if not match:
            raise ValueError(f"Référence non certifiée : {value}")
        parent = outputs.get(match.group(1))
        identifier = parent.get("id") if isinstance(parent, dict) else None
        if isinstance(identifier, bool) or not isinstance(identifier, int) or identifier <= 0:
            raise ValueError(f"Résultat parent invalide : {match.group(1)}")
        return identifier
