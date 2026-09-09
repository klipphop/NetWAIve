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
        allowed = {"netbox_get_objects", "netbox_get_object_by_id", "netbox_get_changelogs", "netbox_search_objects", "netbox_inspect_tree", "netbox_resolve_reference", "netbox_get_endpoint_schema", "netbox_find_available_ip", "netbox_batch_execute"}
        return [{"type": "function", "function": {"name": t["name"], "description": t.get("description", ""), "parameters": t.get("inputSchema", {"type": "object", "properties": {}})}} for t in tools if t.get("name") in allowed]

    @staticmethod
    def _is_allowed_read(name: str) -> bool:
        return name in {"netbox_get_objects", "netbox_get_object_by_id", "netbox_get_changelogs", "netbox_search_objects", "netbox_inspect_tree", "netbox_resolve_reference", "netbox_get_endpoint_schema", "netbox_find_available_ip"}

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

    def _review_coverage(self, request: str, plan: ChangePlan) -> tuple[bool, list[str]]:
        review_prompt = (
            "Tu es un validateur strict de couverture d'un ChangePlan NetBox. "
            "Compare la demande à toutes les opérations. Chaque objet, relation, quantité, attribution, interface, adresse, et mise à jour explicitement demandés doit être représenté. "
            "Réponds uniquement en JSON: {\"complete\":true|false,\"missing\":[\"...\"]}. "
            "Ne juge pas les choix techniques déjà validés par le backend.\n\n"
            f"DEMANDE:\n{request}\n\nPLAN:\n{plan.model_dump_json()}"
        )
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": review_prompt}],
            response_format={"type": "json_object"},
        )
        payload = json.loads(response.choices[0].message.content or "{}")
        return bool(payload.get("complete")), [str(item) for item in payload.get("missing", [])]

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
                        self.mcp.call("netbox_batch_execute", {**plan.mcp_arguments(), "dry_run": True})
                        complete, missing = self._review_coverage(message, plan)
                        if not complete:
                            raise ValueError("ChangePlan incomplet, exigences manquantes: " + "; ".join(missing))
                        return AgentResponse(message=f"Change Plan prêt : {plan.count} opération(s) NetBox regroupée(s).", change_plan=plan, tool_results=observations)
                    except Exception as exc:
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

    def confirm(self, plan: ChangePlan) -> AgentResponse:
        result = ToolResult(ok=True, message="netbox_batch_execute executed")
        try:
            data = self.mcp.call("netbox_batch_execute", plan.mcp_arguments())
            result.data = data
        except Exception as exc:
            detail = str(exc).strip()
            result = ToolResult(ok=False, message=f"netbox_batch_execute failed: {detail}")
        message = "Batch NetBox exécuté." if result.ok else f"Le batch NetBox a échoué : {result.message.removeprefix('netbox_batch_execute failed: ')}"
        return AgentResponse(message=message, tool_results=[result], change_plan=plan)
