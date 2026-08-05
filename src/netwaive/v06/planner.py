from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from ..models import NDX_COMPONENT_ENDPOINTS, PendingToolCall
from .contracts import CertifiedPlanInput, PendingPlan, RouteKind


class DeterministicPlanner:
    """Pure planner: ne lit pas NetBox, ne nettoie pas et ne devine pas."""

    def build(self, certified: CertifiedPlanInput) -> PendingPlan:
        expected_fingerprint = intent_fingerprint(certified.intent.request_text, certified.intent)
        if certified.fingerprint != expected_fingerprint:
            raise ValueError("Fingerprint d’intent invalide.")
        if not certified.intent.resolved:
            raise ValueError("Intent non certifiée par le resolver read-only.")
        if certified.route.kind == RouteKind.CLARIFICATION:
            raise ValueError("Intent non routable.")
        if certified.route.kind == RouteKind.NDX_IMPORT:
            record = certified.route.ndx_record
            if not isinstance(record, dict):
                raise ValueError("Payload NDX absent malgré le routage NDX.")
            calls = [PendingToolCall(id="ndx-import", name="import_ndx_object", arguments={"payload": record})]
        else:
            calls = self._custom_calls(certified)
        return PendingPlan(session_id=certified.session_id, generation=certified.generation, fingerprint=certified.fingerprint, calls=calls)

    def _custom_calls(self, certified: CertifiedPlanInput) -> list[PendingToolCall]:
        intent = certified.intent
        manufacturer = intent.refs.get("manufacturer")
        if manufacturer is None:
            raise ValueError("Manufacturer non résolu en lecture seule.")
        model = str(intent.model or intent.name or "").strip()
        if not model:
            raise ValueError("Modèle absent de l’intent certifiée.")
        parent_id = "device-type"
        calls = [PendingToolCall(
            id=parent_id,
            name="netbox_write",
            arguments={"app": "dcim", "endpoint": "device-types", "action": "create", "data": {"model": model, "manufacturer": manufacturer.id}},
        )]
        components = self._components(intent.component_templates, intent.explicit_count)
        for index, (endpoint, data) in enumerate(components, start=1):
            calls.append(PendingToolCall(
                id=f"component-{index}",
                name="netbox_write",
                arguments={"app": "dcim", "endpoint": endpoint, "action": "create", "data": {**data, "device_type": "${device-type.data.id}"}},
            ))
        return calls

    @staticmethod
    def _components(spec: dict[str, list[dict[str, Any]]], requested: int | None) -> list[tuple[str, dict[str, Any]]]:
        flattened: list[tuple[str, dict[str, Any]]] = []
        for collection, values in spec.items():
            endpoint = NDX_COMPONENT_ENDPOINTS.get(collection)
            if endpoint is None:
                raise ValueError(f"Collection de composants inconnue: {collection}")
            for value in values:
                data = dict(value)
                if "quantity" in data:
                    quantity = data.pop("quantity")
                elif "count" in data:
                    quantity = data.pop("count")
                elif "qty" in data:
                    quantity = data.pop("qty")
                else:
                    quantity = 1
                if isinstance(quantity, bool) or not isinstance(quantity, int) or not 1 <= quantity <= 512:
                    raise ValueError("Quantité de composants invalide.")
                base = str(data.get("name") or "Component").strip()
                for index in range(1, quantity + 1):
                    item = dict(data)
                    item["name"] = re.sub(r"\d+$", str(index), base) if re.search(r"\d+$", base) else f"{base} {index}"
                    flattened.append((endpoint, item))
        if requested is not None and flattened and len(flattened) != requested:
            endpoint, template = flattened[0]
            base = dict(template)
            base_name = str(base.get("name") or "Component")
            flattened = []
            for index in range(1, requested + 1):
                item = dict(base)
                item["name"] = re.sub(r"\d+$", str(index), base_name) if re.search(r"\d+$", base_name) else f"{base_name} {index}"
                flattened.append((endpoint, item))
        return flattened


def intent_fingerprint(request_text: str, intent: Any) -> str:
    payload = json.dumps({"request": request_text, "intent": intent.model_dump(mode="json")}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()
