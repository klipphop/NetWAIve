from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .models import NDX_COMPONENT_ENDPOINTS, NDXImportPayload, NDX_OBJECT_CONFIG, ToolResult


class NDXCompositeImporter:
    """Exécute un DTO NDX validé vers les endpoints NetBox parent/enfants."""

    ENDPOINTS = NDX_COMPONENT_ENDPOINTS

    def __init__(
        self,
        find_existing: Callable[[dict[str, Any]], ToolResult | None],
        execute: Callable[[str, dict[str, Any]], ToolResult],
    ):
        self.find_existing = find_existing
        self.execute = execute

    def _create_or_reuse(self, endpoint: str, data: dict[str, Any]) -> ToolResult:
        arguments = {"app": "dcim", "endpoint": endpoint, "action": "create", "data": data}
        return self.find_existing(arguments) or self.execute("netbox_write", arguments)

    def run(self, raw_payload: dict[str, Any]) -> ToolResult:
        payload = NDXImportPayload.model_validate(raw_payload)
        config = NDX_OBJECT_CONFIG[payload.object_type]
        if payload.component_count() == 0:
            return ToolResult(ok=False, message="Import NDX refusé : la spec ne contient aucun composant.", data={"reason": "empty_component_templates"})
        if config["requires_interfaces"] and payload.interface_count() == 0:
            return ToolResult(ok=False, message="Import NDX refusé : la spec ne contient aucune interface.", data={"reason": "empty_interface_templates"})

        manufacturer = self._create_or_reuse("manufacturers", {"name": payload.manufacturer})
        if not manufacturer.ok:
            return manufacturer
        manufacturer_id = manufacturer.data.get("id") if isinstance(manufacturer.data, dict) else None
        if not isinstance(manufacturer_id, int) or manufacturer_id <= 0:
            return ToolResult(ok=False, message="Import NDX interrompu : le fabricant n’a retourné aucun identifiant.")

        parent_data = payload.parent.model_dump(exclude={"front_image", "rear_image"}, exclude_none=True)
        parent_data["manufacturer"] = manufacturer_id
        parent = self._create_or_reuse(config["endpoint"], parent_data)
        if not parent.ok:
            return parent
        parent_id = parent.data.get("id") if isinstance(parent.data, dict) else None
        if not isinstance(parent_id, int) or parent_id <= 0:
            return ToolResult(ok=False, message=f"Import NDX interrompu : le {config['label']} n’a retourné aucun identifiant.")

        processed = 0
        for collection, endpoint in self.ENDPOINTS.items():
            for component in payload.component_templates.get(collection, []):
                result = self._create_or_reuse(endpoint, {**component, config["relation"]: parent_id})
                if not result.ok:
                    return result
                processed += 1
        return ToolResult(ok=True, message=f"Import NDX {config['label']} terminé : {processed} templates traités.", data={"id": parent_id, "object_type": payload.object_type, "templates_processed": processed})
