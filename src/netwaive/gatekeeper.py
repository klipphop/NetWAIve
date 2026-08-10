from __future__ import annotations

import json
from typing import Any

from openai import OpenAI

from .mcp_client import MCPClient
from .models import AgentResponse, PendingToolCall, ToolResult


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
        messages: list[dict[str, Any]] = [{"role": "system", "content": "You are a NetBox assistant. Use MCP tools. Read tools execute immediately. Never claim a write succeeded before it is confirmed. Answer in the user's language."}]
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
                return AgentResponse(message="Opération(s) d’écriture en attente de confirmation.", pending_confirmation=pending, tool_results=results)
        raise RuntimeError("LLM tool loop exceeded max_turns")

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
