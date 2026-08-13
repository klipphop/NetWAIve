from __future__ import annotations

import json
from typing import Any

from openai import OpenAI

from .mcp_client import MCPClient
from .models import AgentResponse, PendingToolCall, ToolResult


SYSTEM_PROMPT = """You are NetWAIve v0.1.3, a NetBox MCP assistant.

READ requests (list, search, show, get, hello) use read-only MCP tools immediately and never create a pending write.

For ambiguous abbreviations, aliases, or acronyms, reason from the full user context and cross-reference all mentioned objects. When a related model or product family exists in NetBox, inspect its manufacturer before proposing a new one. Never hardcode vendor, model, or alias rules.
If read results and contextual reasoning leave a legitimate doubt, ask one concise natural-language clarification question. Do not guess or create an object while the identity is uncertain.

1. Complete all required read lookups first.
2. Before creating a Manufacturer or Device Type, MUST search NetBox broadly with the exact term, normalized partial terms, meaningful numeric/model tokens, and any aliases supplied by the user. Only propose creation after confirming no equivalent exists; never hardcode vendor-specific names or model rules.
3. If a required prerequisite is missing, autonomously include its creation when the user's intent is to create the complete object.
4. Produce every write Tool Call needed for the complete request before confirmation, preserving dependency order.
5. Never claim a write succeeded before the user confirms and the MCP result is successful.

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
                return AgentResponse(message=assistant.content or "", pending_confirmation=pending, tool_results=results)
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

    def confirm(self, calls: list[PendingToolCall]) -> AgentResponse:
        results: list[ToolResult] = []
        for call in calls:
            try:
                data = self.mcp.call(call.name, call.arguments)
                result = ToolResult(ok=True, message=f"{call.name} executed", data=data)
            except Exception as exc:
                result = ToolResult(ok=False, message=f"{call.name} failed: {exc}")
            results.append(result)
            if not result.ok:
                break
        ok = bool(results) and all(item.ok for item in results) and len(results) == len(calls)
        return AgentResponse(message="Opération exécutée." if ok else "Opération bloquée ou partiellement échouée.", tool_results=results)
