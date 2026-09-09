from __future__ import annotations

import json
import re
from typing import Any

from openai import OpenAI

from .mcp_client import MCPClient
from .contracts import ChangePlan
from .models import AgentResponse, PendingToolCall, ToolResult
from .prompt import SYSTEM_PROMPT


OBJECT_LABELS = {
    "dcim.site": "site",
    "dcim.device": "device",
    "dcim.devicetype": "Device Type",
    "dcim.manufacturer": "Manufacturer",
    "ipam.vlangroup": "VLAN Group",
    "ipam.vlan": "VLAN",
    "ipam.prefix": "préfixe",
    "ipam.ipaddress": "adresse IP",
    "virtualization.virtualmachine": "machine virtuelle",
}


class GatekeeperAgent:
    """Thin LLM/MCP adapter: RO executes, RW waits for explicit confirmation."""

    def __init__(self, client: OpenAI, mcp: MCPClient, model: str, max_turns: int = 8):
        self.client, self.mcp, self.model, self.max_turns = client, mcp, model, max_turns

    @staticmethod
    def _openai_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        visible = [t for t in tools if t.get("name") not in {"netbox_create_object", "netbox_update_object", "netbox_delete_object"}]
        return [{"type": "function", "function": {"name": t["name"], "description": t.get("description", ""), "parameters": t.get("inputSchema", {"type": "object", "properties": {}})}} for t in visible]

    @staticmethod
    def _is_write(name: str) -> bool:
        return name == "netbox_batch_execute" or any(word in name.casefold() for word in ("create", "update", "delete"))

    @staticmethod
    def _normalize_batch_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
        raw = arguments.get("operations", arguments.get("calls"))
        if not isinstance(raw, list) or not raw:
            raise ValueError("netbox_batch_execute requires a non-empty operations list")
        operations = []
        for index, item in enumerate(raw):
            if not isinstance(item, dict):
                raise ValueError(f"batch operation {index} must be an object")
            method = item.get("method")
            endpoint = item.get("endpoint")
            data = item.get("data")
            if method not in {"POST", "PATCH", "DELETE"}:
                raise ValueError(f"batch operation {index} has invalid HTTP method")
            if not isinstance(endpoint, str) or not endpoint.strip():
                raise ValueError(f"batch operation {index} has invalid endpoint")
            if not isinstance(data, dict):
                raise ValueError(f"batch operation {index} requires a data object")
            operations.append({"method": method, "endpoint": endpoint, "data": data})
        return {"operations": operations}

    @classmethod
    def _normalize_write_call(cls, call: PendingToolCall) -> PendingToolCall:
        if call.name != "netbox_batch_execute":
            return call
        return call.model_copy(update={"arguments": cls._normalize_batch_arguments(call.arguments)})
    def run(self, message: str, history: list[dict[str, Any]] | None = None) -> AgentResponse:
        messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
        for item in (history or [])[-16:]:
            if item.get("role") in {"user", "assistant"}:
                messages.append({"role": item["role"], "content": str(item.get("content") or item.get("text") or "")})
        messages.append({"role": "user", "content": message})
        tools = self._openai_tools(self.mcp.tools())
        pending: list[PendingToolCall] = []
        results: list[ToolResult] = []
        inspected = False
        for _ in range(self.max_turns):
            response = self.client.chat.completions.create(model=self.model, messages=messages, tools=tools, tool_choice="auto")
            assistant = response.choices[0].message
            calls = list(assistant.tool_calls or [])
            if not calls:
                text, quick_replies = self._extract_quick_replies(assistant.content or "")
                return AgentResponse(message=text, pending_confirmation=pending, tool_results=results, quick_replies=quick_replies)
            messages.append(assistant.model_dump(exclude_none=True))
            for call in calls:
                arguments = json.loads(call.function.arguments or "{}")
                if self._is_write(call.function.name):
                    candidate = PendingToolCall(id=call.id, name=call.function.name, arguments=arguments)
                    try:
                        normalized = self._normalize_write_call(candidate)
                        if normalized.name == "netbox_batch_execute" and not inspected:
                            raise ValueError("Graph-First requires a read-only MCP inspection before the batch")
                        pending.append(normalized)
                    except ValueError as exc:
                        results.append(ToolResult(ok=False, message=f"Batch invalide : {exc}"))
                        messages.append({"role": "tool", "tool_call_id": call.id, "content": json.dumps({"error": str(exc)})})
                    continue
                try:
                    data = self.mcp.call(call.function.name, arguments)
                    if call.function.name in {"netbox_inspect_tree", "netbox_get_objects", "netbox_search_objects", "netbox_get_object_by_id"}:
                        inspected = True
                    result = ToolResult(ok=True, message="MCP read completed", data=data)
                except Exception as exc:
                    result = ToolResult(ok=False, message=f"MCP read failed: {exc}")
                results.append(result)
                messages.append({"role": "tool", "tool_call_id": call.id, "content": result.model_dump_json()})
            if pending:
                return AgentResponse(message=self._pending_message(pending), pending_confirmation=pending, tool_results=results)
        raise RuntimeError("LLM tool loop exceeded max_turns")

    @staticmethod
    def _extract_quick_replies(text: str) -> tuple[str, list[str]]:
        match = re.search(r"\s*\[OPTIONS:\s*(.*?)\]\s*$", text, re.IGNORECASE | re.DOTALL)
        if not match:
            return text, []
        options = [item.strip() for item in match.group(1).split("|") if item.strip()]
        return text[:match.start()].rstrip(), options

    @staticmethod
    def _pending_message(calls: list[PendingToolCall]) -> str:
        if len(calls) == 1 and calls[0].name == "netbox_batch_execute":
            operations = calls[0].arguments.get("operations") or calls[0].arguments.get("calls") or []
            return f"Change Plan prêt : {len(operations)} opération(s) NetBox regroupée(s)."
        lines = ["Change Plan prêt :"]
        for call in calls:
            lines.append(f"• {GatekeeperAgent._describe_call(call)}")
        return "\n".join(lines)

    @staticmethod
    def _describe_call(call: PendingToolCall) -> str:
        label = OBJECT_LABELS.get(call.arguments.get("object_type", ""), "objet NetBox")
        action = call.name.rsplit("_", 2)[1] if call.name.startswith("netbox_") else "traiter"
        data = call.arguments.get("data") or {}
        if action == "create":
            name = data.get("name") or data.get("model") or data.get("address") or data.get("vid")
            details = []
            if data.get("slug"):
                details.append(f"slug: {data['slug']}")
            if data.get("vid") is not None:
                details.append(f"VID: {data['vid']}")
            subject = f" : {name}" if name is not None else ""
            suffix = f" ({', '.join(details)})" if details else ""
            return f"Créer le {label}{subject}{suffix}"
        identifier = call.arguments.get("object_id", "identifiant inconnu")
        if action == "delete":
            return f"Supprimer le {label} (ID : {identifier})"
        changed = data.get("name") or data.get("model") or data.get("description")
        suffix = f" : {changed}" if changed is not None else ""
        return f"Modifier le {label} (ID : {identifier}){suffix}"

    def confirm(self, calls: list[PendingToolCall], message: str | None = None, history: list[dict[str, Any]] | None = None) -> AgentResponse:
        if not calls:
            return AgentResponse(message="Aucune opération en attente.")
        results: list[ToolResult] = []
        if len(calls) == 1 and calls[0].name == "netbox_batch_execute":
            call = calls[0]
            try:
                plan = ChangePlan(summary="Change Plan NetBox", operations=call.arguments.get("operations", []))
                data = self.mcp.call(call.name, plan.mcp_arguments())
                result = ToolResult(ok=True, message="netbox_batch_execute executed", data=data)
            except Exception as exc:
                result = ToolResult(ok=False, message=f"netbox_batch_execute failed: {exc}")
            results.append(result)
            return AgentResponse(message="Batch NetBox exécuté." if result.ok else "Le batch NetBox a échoué.", tool_results=results)
        first = calls[0]
        try:
            data = self.mcp.call(first.name, first.arguments)
            first_result = ToolResult(ok=True, message=f"{first.name} executed", data=data)
        except Exception as exc:
            first_result = ToolResult(ok=False, message=f"{first.name} failed: {exc}")
        results.append(first_result)
        if not first_result.ok:
            return AgentResponse(message="La première étape a échoué ; les étapes suivantes n’ont pas été exécutées.", tool_results=results)

        messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
        for item in (history or [])[-16:]:
            if item.get("role") in {"user", "assistant"}:
                messages.append({"role": item["role"], "content": str(item.get("content") or item.get("text") or "")})
        messages.append({"role": "user", "content": message or "Poursuis le plan validé jusqu’à sa finalisation."})
        messages.append({"role": "assistant", "content": "", "tool_calls": [{"id": first.id, "type": "function", "function": {"name": first.name, "arguments": json.dumps(first.arguments)}}]})
        messages.append({"role": "tool", "tool_call_id": first.id, "content": first_result.model_dump_json()})
        tools = self._openai_tools(self.mcp.tools())

        for _ in range(self.max_turns):
            response = self.client.chat.completions.create(model=self.model, messages=messages, tools=tools, tool_choice="auto")
            assistant = response.choices[0].message
            next_calls = list(assistant.tool_calls or [])
            if not next_calls:
                text, quick_replies = self._extract_quick_replies(assistant.content or "Opération exécutée.")
                return AgentResponse(message=text, tool_results=results, quick_replies=quick_replies)
            messages.append(assistant.model_dump(exclude_none=True))
            for call in next_calls:
                arguments = json.loads(call.function.arguments or "{}")
                try:
                    data = self.mcp.call(call.function.name, arguments)
                    result = ToolResult(ok=True, message=f"{call.function.name} executed", data=data)
                except Exception as exc:
                    result = ToolResult(ok=False, message=f"{call.function.name} failed: {exc}")
                results.append(result)
                messages.append({"role": "tool", "tool_call_id": call.id, "content": result.model_dump_json()})
                if not result.ok:
                    return AgentResponse(message="Une étape du plan a échoué ; l’exécution est arrêtée.", tool_results=results)
        return AgentResponse(message="Le plan a atteint la limite d’enchaînement ; vérification requise.", tool_results=results)
