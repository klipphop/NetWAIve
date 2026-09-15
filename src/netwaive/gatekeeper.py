from __future__ import annotations

import json
import re
from typing import Any

from openai import OpenAI

from .contracts import ChangePlan
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
    def _batch(args: dict[str, Any]) -> ChangePlan:
        raw = args.get("operations", args.get("calls"))
        if not isinstance(raw, list) or not raw:
            raise ValueError("netbox_batch_execute requires a non-empty operations list")
        return ChangePlan(summary="Change Plan NetBox", operations=raw)

    @staticmethod
    def _is_write(name: str) -> bool:
        return name == "netbox_batch_execute"

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
