from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .models import NDX_COMPONENT_ENDPOINTS, NDX_COMPONENT_RELATIONS, NDXImportPayload, NDX_OBJECT_CONFIG, ToolResult, netbox_slug


class NDXCompositeImporter:
    """Exécute un DTO NDX validé vers les endpoints NetBox parent/enfants."""

    ENDPOINTS = NDX_COMPONENT_ENDPOINTS

    def __init__(
        self,
        find_existing: Callable[[dict[str, Any]], ToolResult | None],
        execute: Callable[[str, dict[str, Any]], ToolResult],
        find_existing_parent: Callable[[str, str, str], ToolResult | None] | None = None,
    ):
        self.find_existing = find_existing
        self.execute = execute
        self.find_existing_parent = find_existing_parent

    def _create_or_reuse(self, endpoint: str, data: dict[str, Any]) -> ToolResult:
        arguments = {"app": "dcim", "endpoint": endpoint, "action": "create", "data": data}
        return self.find_existing(arguments) or self.execute("netbox_write", arguments)

    def run(self, raw_payload: dict[str, Any]) -> ToolResult:
        payload = NDXImportPayload.model_validate(raw_payload)
        config = NDX_OBJECT_CONFIG[payload.object_type]
        existing_parent = (
            self.find_existing_parent(payload.parent.model, payload.object_type, payload.manufacturer)
            if self.find_existing_parent is not None
            else self.find_existing({
                "app": "dcim", "endpoint": config["endpoint"], "action": "create", "data": {"model": payload.parent.model},
            })
        )
        record: dict[str, Any] = {}
        if existing_parent is not None:
            record = existing_parent.data if isinstance(existing_parent.data, dict) else {}
            manufacturer = record.get("manufacturer")
            manufacturer_name = str(manufacturer.get("name") or manufacturer.get("display") or manufacturer.get("slug") or "") if isinstance(manufacturer, dict) else ""
            record_model = str(record.get("model") or "")
            if record_model.casefold() != payload.parent.model.casefold() or manufacturer_name.casefold() != payload.manufacturer.casefold():
                existing_parent = None
        if existing_parent is not None:
            return ToolResult(
                ok=True,
                message=f"{config['label']} déjà présent ; import NDX ignoré.",
                data={"id": record.get("id"), "skipped": True, "object_type": payload.object_type},
            )
        if payload.component_count() == 0:
            return ToolResult(ok=False, message="Import NDX refusé : la spec ne contient aucun composant.", data={"reason": "empty_component_templates"})
        if config["requires_interfaces"] and payload.interface_count() == 0:
            return ToolResult(ok=False, message="Import NDX refusé : la spec ne contient aucune interface.", data={"reason": "empty_interface_templates"})

        try:
            manufacturer_data = {"name": payload.manufacturer, "slug": netbox_slug(payload.manufacturer)}
        except ValueError as exc:
            return ToolResult(ok=False, message=f"Import NDX refusé : {exc}", data={"reason": "invalid_manufacturer_slug"})
        manufacturer = self._create_or_reuse("manufacturers", manufacturer_data)
        if not manufacturer.ok:
            return manufacturer
        manufacturer_id = manufacturer.data.get("id") if isinstance(manufacturer.data, dict) else None
        if not isinstance(manufacturer_id, int) or manufacturer_id <= 0:
            return ToolResult(ok=False, message="Import NDX interrompu : le fabricant n’a retourné aucun identifiant.")

        parent_data = payload.parent.model_dump(exclude={"front_image", "rear_image"}, exclude_none=True)
        try:
            parent_data["slug"] = netbox_slug(parent_data.get("slug") or parent_data.get("model"))
        except ValueError as exc:
            return ToolResult(ok=False, message=f"Import NDX refusé : {exc}", data={"reason": "invalid_parent_slug"})
        if payload.object_type == "module-type":
            parent_data.pop("slug", None)
        parent_data["manufacturer"] = manufacturer_id
        parent = self._create_or_reuse(config["endpoint"], parent_data)
        if not parent.ok:
            return parent
        parent_id = parent.data.get("id") if isinstance(parent.data, dict) else None
        if not isinstance(parent_id, int) or parent_id <= 0:
            return ToolResult(ok=False, message=f"Import NDX interrompu : le {config['label']} n’a retourné aucun identifiant.")

        queue = [
            (collection, endpoint, dict(component))
            for collection, endpoint in self.ENDPOINTS.items()
            for component in payload.component_templates.get(collection, [])
        ]
        resolved: dict[tuple[str, str], int] = {}
        processed = 0
        while queue:
            deferred = []
            progress = False
            for collection, endpoint, component in queue:
                data = dict(component)
                unresolved = False
                for field, target_collection in NDX_COMPONENT_RELATIONS.get(collection, {}).items():
                    reference = data.get(field)
                    if isinstance(reference, str):
                        target_id = resolved.get((target_collection, reference))
                        if target_id is None:
                            unresolved = True
                            break
                        data[field] = target_id
                if unresolved:
                    deferred.append((collection, endpoint, component))
                    continue
                result = self._create_or_reuse(endpoint, {**data, config["relation"]: parent_id})
                if not result.ok:
                    return result
                component_id = result.data.get("id") if isinstance(result.data, dict) else None
                if not isinstance(component_id, int) or component_id <= 0:
                    return ToolResult(ok=False, message=f"Import NDX interrompu : le template {component.get('name')} n’a retourné aucun identifiant.")
                resolved[(collection, str(component["name"]))] = component_id
                processed += 1
                progress = True
            if deferred and not progress:
                names = [str(component.get("name")) for _, _, component in deferred]
                return ToolResult(ok=False, message=f"Import NDX interrompu : références de composants non résolues pour {', '.join(names)}.", data={"unresolved_components": names})
            queue = deferred
        return ToolResult(ok=True, message=f"Import NDX {config['label']} terminé : {processed} templates traités.", data={"id": parent_id, "object_type": payload.object_type, "templates_processed": processed})
