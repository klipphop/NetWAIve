from __future__ import annotations

import json
import re
from typing import Any

from openai import OpenAI

from .contracts import ChangePlan
from .mcp_client import MCPClient
from .models import AgentResponse, ToolResult
from .prompt import SYSTEM_PROMPT


class GatekeeperAgent:
    """MCP-first expert: reads execute immediately; one ChangePlan gates writes."""

    def __init__(self, client: OpenAI, mcp: MCPClient, model: str, max_turns: int = 8):
        self.client, self.mcp, self.model, self.max_turns = client, mcp, model, max_turns

    @staticmethod
    def _openai_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        hidden = {"netbox_create_object", "netbox_update_object", "netbox_delete_object"}
        return [{"type": "function", "function": {"name": t["name"], "description": t.get("description", ""), "parameters": t.get("inputSchema", {"type": "object", "properties": {}})}} for t in tools if t.get("name") not in hidden]

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
                try:
                    data = self.mcp.call(call.function.name, args)
                    inspected |= call.function.name in {"netbox_inspect_tree", "netbox_get_objects", "netbox_search_objects", "netbox_get_object_by_id"}
                    result = ToolResult(ok=True, message="MCP read completed", data=data)
                except Exception as exc:
                    result = ToolResult(ok=False, message=f"MCP read failed: {exc}")
                observations.append(result)
                messages.append({"role": "tool", "tool_call_id": call.id, "content": result.model_dump_json()})
        raise RuntimeError("LLM tool loop exceeded max_turns")

    def confirm(self, plan: ChangePlan) -> AgentResponse:
        result = ToolResult(ok=True, message="netbox_batch_execute executed")
        try:
            data = self.mcp.call("netbox_batch_execute", plan.mcp_arguments())
            result.data = data
        except Exception as exc:
            result = ToolResult(ok=False, message=f"netbox_batch_execute failed: {exc}")
        return AgentResponse(message="Batch NetBox exécuté." if result.ok else "Le batch NetBox a échoué.", tool_results=[result], change_plan=plan)
