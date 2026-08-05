from __future__ import annotations

import json
from typing import Any

from openai import OpenAI

from ..models import GetEndpointSchemaArgs, NetBoxReadArgs, ToolResult
from .contracts import IntentResolution, ResolutionKind
from .resolver import ReadOnlyResolver


INTENT_PROMPT = """You are the v0.6 intent extractor. You may only use netbox_read and get_endpoint_schema. Resolve no writes. Return JSON only: {model, name, refs:{site, manufacturer, device_type, module_type, rack, device}, explicit_count, component_templates}. Do not invent IDs; refs contain user names only."""


class ReadOnlyIntentExtractor:
    """LLM ReAct loop constrained to read-only tools before planning."""

    def __init__(self, client: OpenAI, tools: Any, resolver: ReadOnlyResolver, model: str, max_turns: int = 8):
        self.client, self.tools, self.resolver, self.model = client, tools, resolver, model
        self.max_turns = max_turns

    def extract(self, request_text: str) -> IntentResolution:
        messages: list[dict[str, Any]] = [{"role": "system", "content": INTENT_PROMPT}, {"role": "user", "content": request_text}]
        schemas = [schema for schema in self.tools.tool_schemas() if schema.get("function", {}).get("name") in {"netbox_read", "get_endpoint_schema"}]
        for _ in range(self.max_turns):
            response = self.client.chat.completions.create(model=self.model, messages=messages, tools=schemas, tool_choice="auto")
            assistant = response.choices[0].message
            calls = list(assistant.tool_calls or [])
            if not calls:
                draft = self._json(assistant.content or "")
                refs = {ResolutionKind(key): value for key, value in (draft.get("refs") or {}).items() if key in {kind.value for kind in ResolutionKind if kind != ResolutionKind.GENERIC} and isinstance(value, str) and value.strip()}
                return self.resolver.resolve_intent(request_text, model=draft.get("model"), name=draft.get("name"), refs=refs, explicit_count=draft.get("explicit_count"), component_templates=draft.get("component_templates"))
            messages.append(assistant.model_dump(exclude_none=True))
            for call in calls:
                args = json.loads(call.function.arguments or "{}")
                if call.function.name == "netbox_read":
                    result = self.tools.netbox_read(NetBoxReadArgs.model_validate(args))
                else:
                    result = self.tools.get_endpoint_schema(GetEndpointSchemaArgs.model_validate(args))
                messages.append({"role": "tool", "tool_call_id": call.id, "content": result.model_dump_json()})
        raise ValueError("La phase read-only n’a pas produit d’intention structurée.")

    @staticmethod
    def _json(content: str) -> dict[str, Any]:
        try:
            value = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValueError("Réponse d’intention JSON invalide.") from exc
        if not isinstance(value, dict):
            raise ValueError("Intention structurée invalide.")
        return value
