from __future__ import annotations

import json
import re
from typing import Any

from openai import OpenAI

from .mcp_client import MCPClient
from .models import AgentResponse, PendingToolCall, ToolResult


SYSTEM_PROMPT = """You are NetWAIve v0.1.5, a NetBox MCP assistant.

READ requests (list, search, show, get, hello) use read-only MCP tools immediately and never create a pending write.

For ambiguous abbreviations, aliases, or acronyms, reason from the full user context and cross-reference all mentioned objects. When a related model or product family exists in NetBox, inspect its manufacturer before proposing a new one. Never hardcode vendor, model, or alias rules.
If clarification is required, ask one concise question and append machine-readable choices as `[OPTIONS: option A | option B]`; the adapter removes this marker and exposes the choices as quick replies.

1. Complete all required read lookups first.
2. Before creating a Manufacturer or Device Type, MUST search NetBox broadly with the exact term, normalized partial terms, meaningful numeric/model tokens, and any aliases supplied by the user. Only propose creation after confirming no equivalent exists; never hardcode vendor-specific names or model rules.
4. For a dcim.device creation, always provide name (string), site (positive integer ID), device_type (positive integer ID), role (positive integer device-role ID), and status (active by default or planned). To assign management IP: create the device first, create the IP with active status, then optionally attach it to the management interface and set primary_ip4.
5. Produce every write Tool Call needed for the complete request before confirmation, preserving dependency order.
6. Never claim a write succeeded before the user confirms and the MCP result is successful.

The UI renders the confirmation plan in natural language. Do not put raw JSON, function calls, object_type, or argument dictionaries in user-facing text.
Answer in the user's language.
"""

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
        return [{"type": "function", "function": {"name": t["name"], "description": t.get("description", ""), "parameters": t.get("inputSchema", {"type": "object", "properties": {}})}} for t in tools]

    @staticmethod
    def _is_write(name: str) -> bool:
        return any(word in name.casefold() for word in ("create", "update", "delete"))

    def run(self, message: str, history: list[dict[str, Any]] | None = None) -> AgentResponse:
        messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
        for item in (history or [])[-16:]:
            if item.get("role") in {"user", "assistant"}:
                messages.append({"role": item["role"], "content": str(item.get("content") or item.get("text") or "")})
        messages.append({"role": "user", "content": message})
        tools = self._openai_tools(self.mcp.tools())
        pending: list[PendingToolCall] = []
        results: list[ToolResult] = []
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
                    pending.append(PendingToolCall(id=call.id, name=call.function.name, arguments=arguments))
                    continue
                try:
                    data = self.mcp.call(call.function.name, arguments)
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
        lines = ["Plan complet en attente de confirmation :"]
        for call in calls:
            lines.append(f"• {GatekeeperAgent._describe_call(call)}")
        lines.append("Confirmez par Oui pour exécuter toutes les étapes dans cet ordre.")
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
