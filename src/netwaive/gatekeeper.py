from __future__ import annotations

import json
import re
from typing import Any

from openai import OpenAI

from .contracts import ChangePlan
from .selection import SelectionResult, selection_from_payload
from .mcp_client import MCPClient
from .schemas import AgentResponse, ToolResult
from .prompt import SYSTEM_PROMPT


class GatekeeperAgent:
    """MCP-first expert: reads execute immediately; one ChangePlan gates writes."""

    def __init__(self, client: OpenAI, mcp: MCPClient, model: str, max_turns: int = 8):
        self.client, self.mcp, self.model, self.max_turns = client, mcp, model, max_turns

    @staticmethod
    def _openai_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        allowed = {"netbox_get_objects", "netbox_get_object_by_id", "netbox_get_changelogs", "netbox_search_objects", "netbox_inspect_tree", "netbox_resolve_reference", "netbox_get_endpoint_schema", "netbox_find_available_ip", "netbox_compare_scoped_relations", "netbox_batch_execute"}
        return [{"type": "function", "function": {"name": t["name"], "description": t.get("description", ""), "parameters": t.get("inputSchema", {"type": "object", "properties": {}})}} for t in tools if t.get("name") in allowed]

    @staticmethod
    def _is_allowed_read(name: str) -> bool:
        return name in {"netbox_get_objects", "netbox_get_object_by_id", "netbox_get_changelogs", "netbox_search_objects", "netbox_inspect_tree", "netbox_resolve_reference", "netbox_get_endpoint_schema", "netbox_find_available_ip", "netbox_compare_scoped_relations"}

    @staticmethod
    def _validate_plan_graph(plan: ChangePlan) -> None:
        """Reject unresolved, forward, cyclic, and unsupported plan references."""
        ref_re = re.compile(r"\$\{(\d+)\.([A-Za-z_][A-Za-z0-9_]*)\}")
        unsupported = re.compile(r"\$\{([^}]+)\}")
        edges: dict[int, set[int]] = {index: set() for index in range(plan.count)}
        for index, operation in enumerate(plan.operations):
            values = [operation.endpoint, json.dumps(operation.data, ensure_ascii=False)]
            for value in values:
                for expression in unsupported.findall(value):
                    if not re.fullmatch(r"\d+\.[A-Za-z_][A-Za-z0-9_]*", expression) and not expression.startswith("available_ip:"):
                        raise ValueError(f"operation {index}: unsupported reference ${{{expression}}}")
                for producer, _field in ref_re.findall(value):
                    producer_index = int(producer)
                    if producer_index >= plan.count:
                        raise ValueError(f"operation {index}: reference points outside the plan: ${{{producer}.{_field}}}")
                    if producer_index >= index:
                        raise ValueError(f"operation {index}: forward/cyclic reference: ${{{producer}.{_field}}}")
                    edges[index].add(producer_index)
        visiting: set[int] = set()
        visited: set[int] = set()
        def visit(node: int) -> None:
            if node in visiting:
                raise ValueError("Change Plan contains a dependency cycle")
            if node in visited:
                return
            visiting.add(node)
            for parent in edges[node]:
                visit(parent)
            visiting.remove(node)
            visited.add(node)
        for node in edges:
            visit(node)

    @staticmethod
    def _batch(args: dict[str, Any]) -> ChangePlan:
        raw = args.get("operations", args.get("calls"))
        if not isinstance(raw, list) or not raw:
            raise ValueError("netbox_batch_execute requires a non-empty operations list")
        selection = selection_from_payload(args.get("selection"))
        if selection is not None and not selection.selected:
            raise ValueError("selection contains no selected items")
        plan = ChangePlan(summary="Change Plan NetBox", operations=raw, selection=selection)
        GatekeeperAgent._validate_plan_graph(plan)
        return plan

    @staticmethod
    def _is_write(name: str) -> bool:
        return name == "netbox_batch_execute"

    @staticmethod
    def _render_relation_result(data: Any) -> str | None:
        if not isinstance(data, dict) or not {"left_count", "right_count", "covered_count", "missing_count", "missing"}.issubset(data):
            return None
        left_count = data.get("left_count")
        right_count = data.get("right_count")
        covered_count = data.get("covered_count")
        missing_count = data.get("missing_count")
        if not all(isinstance(value, int) for value in (left_count, right_count, covered_count, missing_count)) or right_count == 0 or covered_count + missing_count != left_count:
            return "Vérification NetBox impossible : les totaux de la comparaison sont incohérents. Aucune liste fiable n’est présentée."
        lines = [f"Vérification NetBox terminée : {left_count} objets analysés, {right_count} relations analysées.", f"• Couverts : {covered_count}", f"• Sans relation : {missing_count}"]
        missing = data.get("missing") or []
        if missing:
            lines.append("\nObjets sans relation :")
            lines.extend(f"• {item.get('label') or item.get('name') or item.get('id')}" for item in missing)
        return "\n".join(lines)

    @staticmethod
    def _extract(text: str) -> tuple[str, list[str]]:
        match = re.search(r"\s*\[OPTIONS:\s*(.*?)\]\s*$", text, re.I | re.S)
        if not match:
            return text, []
        return text[:match.start()].rstrip(), [x.strip() for x in match.group(1).split("|") if x.strip()]

    def _messages(self, message: str, history: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        out = [{"role": "system", "content": SYSTEM_PROMPT}]
        out.extend({"role": x["role"], "content": str(x.get("content") or x.get("text") or "")} for x in (history or [])[-16:] if x.get("role") in {"user", "assistant"})
        out.append({"role": "user", "content": message})
        return out

    def run(self, message: str, history: list[dict[str, Any]] | None = None) -> AgentResponse:
        messages = self._messages(message, history)
        tools = self._openai_tools(self.mcp.tools())
        inspected = False
        observations: list[ToolResult] = []
        for _ in range(self.max_turns):
            response = self.client.chat.completions.create(model=self.model, messages=messages, tools=tools, tool_choice="auto")
            assistant = response.choices[0].message
            calls = list(assistant.tool_calls or [])
            if not calls:
                for observation in reversed(observations):
                    rendered = self._render_relation_result(observation.data)
                    if rendered:
                        return AgentResponse(message=rendered, tool_results=observations)
                text, quick = self._extract(assistant.content or "")
                return AgentResponse(message=text, tool_results=observations, quick_replies=quick)
            messages.append(assistant.model_dump(exclude_none=True))
            for call in calls:
                args = json.loads(call.function.arguments or "{}")
                if self._is_write(call.function.name):
                    try:
                        plan = self._batch(args)
                        if not inspected:
                            raise ValueError("Graph-First requires a read-only MCP inspection before the batch")
                        dry_run_args = {"operations": [item.model_dump() for item in plan.operations], "dry_run": True}
                        try:
                            self.mcp.call("netbox_batch_execute", dry_run_args)
                        except Exception as exc:
                            raise ValueError(f"Préflight NetBox refusé : {exc}") from exc
                        return AgentResponse(message=f"Change Plan prêt : {plan.count} opération(s) NetBox regroupée(s).", change_plan=plan, tool_results=observations)
                    except ValueError as exc:
                        result = ToolResult(ok=False, message=f"Batch invalide : {exc}")
                        observations.append(result)
                        messages.append({"role": "tool", "tool_call_id": call.id, "content": result.model_dump_json()})
                        continue
                if not self._is_allowed_read(call.function.name):
                    result = ToolResult(ok=False, message=f"Outil MCP non autorisé : {call.function.name}")
                    observations.append(result)
                    messages.append({"role": "tool", "tool_call_id": call.id, "content": result.model_dump_json()})
                    continue
                try:
                    data = self.mcp.call(call.function.name, args)
                    inspected = True
                    result = ToolResult(ok=True, message="MCP read completed", data=data)
                except Exception as exc:
                    result = ToolResult(ok=False, message=f"MCP read failed: {exc}")
                observations.append(result)
                messages.append({"role": "tool", "tool_call_id": call.id, "content": result.model_dump_json()})
        raise RuntimeError("LLM tool loop exceeded max_turns")

    @staticmethod
    def _execution_message(result: ToolResult, plan: ChangePlan) -> str:
        if result.ok:
            payload = result.data if isinstance(result.data, dict) else {}
            rows = payload.get("results", []) if isinstance(payload.get("results"), list) else []
            if not rows:
                return "Opération NetBox terminée avec succès."
            lines = [f"Opération NetBox terminée : {len(rows)} objet(s) traité(s)."]
            for row in rows:
                method = {"POST": "Créé", "PATCH": "Modifié", "DELETE": "Supprimé"}.get(row.get("method"), row.get("method", "Traité"))
                lines.append(f"• {method} {row.get('label') or row.get('endpoint')}")
            return "\n".join(lines)
        detail = result.message.removeprefix("netbox_batch_execute failed: ").strip()
        match = re.search(r"Unable to delete object\. (\d+) dependent objects were found: (.+)", detail, re.I)
        if match:
            count, dependencies = match.groups()
            return (f"Suppression refusée par NetBox : l’objet demandé possède {count} dépendance(s).\n"
                    f"• Dépendances détectées : {dependencies}\n"
                    "Aucune suppression n’a été effectuée. Je peux préparer un plan séparé pour traiter ces dépendances, mais je ne les supprimerai pas automatiquement.")
        return f"Opération NetBox non exécutée : {detail}"

    def confirm(self, plan: ChangePlan) -> AgentResponse:
        result = ToolResult(ok=True, message="netbox_batch_execute executed")
        try:
            data = self.mcp.call("netbox_batch_execute", plan.mcp_arguments())
            result.data = data
        except Exception as exc:
            detail = str(exc).strip()
            result = ToolResult(ok=False, message=f"netbox_batch_execute failed: {detail}")
        message = self._execution_message(result, plan)
        return AgentResponse(message=message, tool_results=[result], change_plan=plan)
