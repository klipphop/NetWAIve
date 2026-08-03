from __future__ import annotations

import json
import re
from itertools import combinations, islice
from typing import Any

import pynetbox
from pynetbox.core.app import App
from pynetbox.core.query import RequestError

from .composite import NDXCompositeImporter
from .config import Settings
from .errors import NetBoxChatError, ObjectNotFound
from .models import (
    GetEndpointSchemaArgs,
    NetBoxReadArgs,
    NetBoxWriteArgs,
    NDXImportPayload,
    ToolResult,
)
from .ndx import NDXConnector


class NetBoxTools:
    """Trois outils universels couvrant dynamiquement l'API NetBox via pynetbox."""

    ARG_MODELS = {
        "netbox_read": NetBoxReadArgs,
        "netbox_write": NetBoxWriteArgs,
        "get_endpoint_schema": GetEndpointSchemaArgs,
    }
    MUTATING_TOOLS = {"netbox_write"}

    def __init__(self, settings: Settings):
        self.settings = settings
        self.api = pynetbox.api(
            settings.netbox_url.rstrip("/"),
            token=settings.netbox_token.get_secret_value(),
            strict_filters=True,
        )
        self.api.http_session.verify = settings.netbox_verify_ssl
        self.ndx = NDXConnector()

    def tool_schemas(self) -> list[dict[str, Any]]:
        descriptions = {
            "netbox_read": (
                "Lire n'importe quel objet NetBox. app et endpoint sont dynamiques. "
                "method vaut filter, all, get ou count. Place les filtres dans kwargs. "
                "Pour une IP libre: app='ipam', endpoint='available_ips', kwargs={'prefix':'CIDR'}. "
                "Pour un plugin tiers: app='plugins', endpoint='plugin_slug/endpoint_slug'."
            ),
            "netbox_write": (
                "Créer, modifier ou supprimer n'importe quel objet NetBox. "
                "update/delete exigent data.id. Toujours découvrir le schéma et les IDs avant l'écriture."
            ),
            "get_endpoint_schema": (
                "Inspecter le schéma OpenAPI live d'un endpoint: méthodes, filtres, champs autorisés et requis."
            ),
        }
        return [
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": descriptions[name],
                    "parameters": model.model_json_schema(),
                },
            }
            for name, model in self.ARG_MODELS.items()
        ]

    @staticmethod
    def _friendly_error(detail: Any) -> str:
        raw = str(detail)
        match = re.search(r"Duplicate termination found for\s+(.+)", raw, re.IGNORECASE)
        if match:
            return f"Cette terminaison est déjà câblée : {match.group(1).strip()}. Choisis une autre interface ou déconnecte le câble existant."
        return raw

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        model = self.ARG_MODELS.get(name)
        method = getattr(self, name, None)
        if model is None or method is None:
            return ToolResult(ok=False, message=f"Outil inconnu : {name}")
        try:
            return method(model.model_validate(arguments))
        except NetBoxChatError as exc:
            return ToolResult(ok=False, message=str(exc))
        except RequestError as exc:
            detail = getattr(exc, "error", None) or str(exc)
            return ToolResult(ok=False, message=self._friendly_error(detail))
        except Exception as exc:
            return ToolResult(ok=False, message=self._friendly_error(exc))

    @staticmethod
    def _safe(value: Any, depth: int = 0) -> Any:
        if depth > 5:
            return str(value)
        if hasattr(value, "serialize"):
            value = value.serialize()
        if isinstance(value, dict):
            return {str(key): NetBoxTools._safe(item, depth + 1) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [NetBoxTools._safe(item, depth + 1) for item in value]
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        return str(value)

    @staticmethod
    def _normalize(value: str) -> str:
        return re.sub(r"[^a-z0-9]", "", value.lower())

    def _split_target(self, app: str, endpoint: str) -> tuple[str, str | None, str | None]:
        app_name = app.strip().strip("/").replace("_", "-")
        endpoint_name = endpoint.strip().strip("/")
        if app_name != "plugins":
            return app_name, None, endpoint_name
        parts = [part for part in re.split(r"[/.]", endpoint_name) if part]
        if not parts:
            raise NetBoxChatError("Le nom du plugin est obligatoire.")
        plugin = parts[0].replace("_", "-")
        resource = "/".join(parts[1:]) if len(parts) > 1 else None
        return "plugins", plugin, resource

    def _openapi_collections(self, app: str, plugin: str | None = None) -> list[dict[str, str]]:
        prefix = f"/api/{app}/" if app != "plugins" else f"/api/plugins/{plugin}/"
        output: list[dict[str, str]] = []
        for path in self.api.openapi().get("paths", {}):
            if not path.startswith(prefix) or "{" in path:
                continue
            remainder = path[len(prefix):].strip("/")
            if remainder and "/" not in remainder:
                output.append({"endpoint": remainder, "path": path})
        return sorted(output, key=lambda item: item["endpoint"])

    def _resolve_endpoint(self, app: str, endpoint: str):
        app_name, plugin, resource = self._split_target(app, endpoint)
        if app_name == "plugins" and resource is None:
            return None, app_name, plugin, None

        collections = self._openapi_collections(app_name, plugin)
        requested = self._normalize(resource or "")
        matches = [item for item in collections if self._normalize(item["endpoint"]) == requested]
        if len(matches) > 1:
            raise NetBoxChatError(f"Endpoint ambigu : {app}/{endpoint}")
        actual = matches[0]["endpoint"] if matches else str(resource or endpoint).replace("_", "-")

        if app_name == "plugins":
            plugin_app = getattr(self.api.plugins, str(plugin).replace("-", "_"))
            return plugin_app.endpoint(actual), app_name, plugin, actual

        try:
            app_obj = getattr(self.api, app_name.replace("-", "_"))
        except AttributeError:
            app_obj = App(self.api, app_name)
        return app_obj.endpoint(actual), app_name, None, actual

    def _plugin_overview(self, plugin: str) -> ToolResult:
        installed = self.api.plugins.installed_plugins()
        matches = [item for item in installed if self._normalize(str(item.get("name") or item.get("package") or "")) == self._normalize(plugin)]
        endpoints = self._openapi_collections("plugins", plugin)
        return ToolResult(
            ok=bool(matches or endpoints),
            message=f"Plugin {plugin} : {len(endpoints)} endpoint(s) API découvert(s).",
            data={"plugin": self._safe(matches), "endpoints": endpoints},
        )

    def _read_available_ips(self, args: NetBoxReadArgs) -> ToolResult:
        kwargs = args.merged_kwargs()
        prefix_id = kwargs.get("prefix_id") or kwargs.get("id")
        prefix_cidr = kwargs.get("prefix") or kwargs.get("prefix_cidr")
        if prefix_id is not None:
            prefix = self.api.ipam.prefixes.get(prefix_id)
        elif prefix_cidr:
            prefix = self.api.ipam.prefixes.get(prefix=str(prefix_cidr))
        else:
            raise NetBoxChatError("available_ips exige kwargs.prefix ou kwargs.prefix_id.")
        if prefix is None:
            raise ObjectNotFound(f"Préfixe absent de NetBox : {prefix_cidr or prefix_id}")
        available = list(islice(prefix.available_ips.list(limit=args.limit), args.limit))
        data = [self._safe(item) for item in available]
        return ToolResult(
            ok=True,
            message=f"{len(data)} adresse(s) disponible(s) dans {prefix}.",
            data=data,
        )

    def _read_ndx(self, args: NetBoxReadArgs) -> ToolResult:
        query = args.merged_kwargs()
        text = query.get("query") or query.get("model") or query.get("part_number")
        matches = self.ndx.search(text)
        return ToolResult(ok=True, message=f"NDX : {len(matches)} correspondance(s).", data={"source":"ndx", "query":str(text or ""), "candidates":matches[:50]})

    def _read_ndx_spec(self, args: NetBoxReadArgs) -> ToolResult:
        query = args.merged_kwargs()
        model = query.get("model") or query.get("part_number")
        candidates = self.ndx.search(model)
        payload = self.ndx.build_payload(model)
        if payload is None:
            return ToolResult(ok=False, message="NDX exige une référence exacte avant import.", data={"candidates": candidates})
        validated = NDXImportPayload.model_validate(payload)
        if validated.interface_count() == 0:
            return ToolResult(ok=False, message="Spec NDX incomplète : aucune interface publiée.", data={"reason": "empty_interface_templates"})
        return ToolResult(ok=True, message="Spec NDX exacte chargée.", data=payload)

    def netbox_read(self, args: NetBoxReadArgs) -> ToolResult:
        """Lecture universelle de n'importe quel endpoint NetBox."""
        if self._normalize(args.app) == "ndx" and self._normalize(args.endpoint) in {"spec", "device-types", "devicetypes"}:
            return self._read_ndx_spec(args)
        if self._normalize(args.app) == "ndx":
            return self._read_ndx(args)
        if self._normalize(args.app) == "ipam" and self._normalize(args.endpoint) in {"availableips", "prefixavailableips"}:
            return self._read_available_ips(args)
        endpoint, app_name, plugin, actual = self._resolve_endpoint(args.app, args.endpoint)
        if endpoint is None and app_name == "plugins" and plugin:
            return self._plugin_overview(plugin)

        kwargs = args.merged_kwargs()
        if self._normalize(args.app) == "ipam" and self._normalize(args.endpoint) in {"prefixes", "prefix"}:
            # NetBox IPAM prefix filtering does not accept the generic site relation filter.
            kwargs.pop("site", None)
            kwargs.pop("site_id", None)
        if args.method == "all":
            records = list(islice(endpoint.all(limit=args.limit), args.limit))
            data: Any = [self._safe(record) for record in records]
        elif args.method == "filter":
            records = list(islice(endpoint.filter(limit=args.limit, **kwargs), args.limit))
            data = [self._safe(record) for record in records]
        elif args.method == "get":
            object_id = kwargs.pop("id", None)
            record = endpoint.get(object_id) if object_id is not None else endpoint.get(**kwargs)
            if record is None:
                raise ObjectNotFound(f"Aucun objet trouvé dans {args.app}/{args.endpoint}.")
            data = self._safe(record)
        elif args.method == "count":
            data = endpoint.count(**kwargs)
        else:
            raise NetBoxChatError(f"Méthode de lecture inconnue : {args.method}")

        count = len(data) if isinstance(data, list) else (data if isinstance(data, int) else 1)
        return ToolResult(
            ok=True,
            message=f"Lecture {args.app}/{args.endpoint} réussie : {count} résultat(s).",
            data=data,
        )

    def find_existing_create(self, arguments: dict[str, Any]) -> ToolResult | None:
        """Live idempotence guard for every generic create before the API mutation."""
        args = NetBoxWriteArgs.model_validate(arguments)
        if args.action != "create":
            return None
        endpoint, _, _, _ = self._resolve_endpoint(args.app, args.endpoint)
        data = dict(args.data)
        identities = {key: data[key] for key in ("name", "slug", "prefix", "address", "vid", "model") if data.get(key) not in (None, "")}
        if not identities:
            return None
        context_ids = {key: value for key, value in data.items() if isinstance(value, int) and not isinstance(value, bool) and key not in {"id", "vid"}}
        records = None
        context_filter_used = False
        relation_filters = [(f"{key}_id", value) for key, value in context_ids.items()]
        for size in range(len(relation_filters), 0, -1):
            if context_filter_used:
                break
            for subset in combinations(relation_filters, size):
                try:
                    scoped_filters = dict(subset)
                    records = list(islice(endpoint.filter(limit=100, **identities, **scoped_filters), 100))
                    context_filter_used = True
                    break
                except Exception:
                    continue
        try:
            if records is None:
                records = list(islice(endpoint.filter(limit=1000, **identities), 1000))
        except Exception:
            return None
        if context_ids:
            scoped = []
            for record in records:
                serialized = self._safe(record)
                if not isinstance(serialized, dict):
                    continue
                valid = True
                for key, expected in context_ids.items():
                    current = serialized.get(key)
                    current_id = current.get("id") if isinstance(current, dict) else current
                    if current_id != expected:
                        valid = False
                        break
                if valid:
                    scoped.append(record)
            records = scoped
        if not records:
            return None
        record = self._safe(records[0])
        return ToolResult(ok=True, message="Objet existant réutilisé.", data=record)

    def preflight_termination_collisions(self, arguments: dict[str, Any]) -> ToolResult | None:
        """Inspect every `*_terminations` relation and reject occupied live endpoints."""
        data = arguments.get("data") if isinstance(arguments.get("data"), dict) else {}
        terms: list[dict[str, Any]] = []
        for key, value in data.items():
            if not key.endswith("terminations"):
                continue
            values = value if isinstance(value, list) else [value]
            terms.extend(item for item in values if isinstance(item, dict))
        for term in terms:
            object_type = str(term.get("object_type") or "")
            object_id = term.get("object_id") or term.get("id")
            if not object_type or not isinstance(object_id, int):
                continue
            app, _, resource = object_type.partition(".")
            candidates = self._openapi_collections(app)
            target = next((item["endpoint"] for item in candidates if self._normalize(item["endpoint"]) in {self._normalize(resource), self._normalize(resource + "s")}), None)
            if not target:
                continue
            endpoint, _, _, _ = self._resolve_endpoint(app, target)
            record = endpoint.get(object_id)
            serialized = self._safe(record) if record else {}
            cable = serialized.get("cable") if isinstance(serialized, dict) else None
            if cable not in (None, "", {}):
                device = serialized.get("device") if isinstance(serialized, dict) else None
                device_name = device.get("name") if isinstance(device, dict) else str(device or "équipement inconnu")
                term_name = serialized.get("name") if isinstance(serialized, dict) else str(object_id)
                cable_id = cable.get("id") if isinstance(cable, dict) else cable
                return ToolResult(ok=False, message=f"L’interface {term_name} sur l’équipement {device_name} est déjà câblée (Câble #{cable_id}). Veuillez choisir une autre interface ou déconnecter l’existante.", data={"occupied_termination": serialized, "cable": cable})
        return None

    def validate_write_payload(self, arguments: dict[str, Any]) -> ToolResult | None:
        """Generic OpenAPI-driven preflight for required fields and enum choices."""
        args = NetBoxWriteArgs.model_validate(arguments)
        if args.action not in {"create", "update"}:
            return None
        schema_result = self.get_endpoint_schema(GetEndpointSchemaArgs(app=args.app, endpoint=args.endpoint))
        if not schema_result.ok:
            return schema_result
        schema = schema_result.data if isinstance(schema_result.data, dict) else {}
        data = dict(args.data)
        missing = [field for field in schema.get("required_fields", []) if field not in data and args.action == "create"]
        if missing:
            details = []
            writable = schema.get("writable_fields", {})
            for field in missing:
                enum = writable.get(field, {}).get("enum") if isinstance(writable.get(field), dict) else None
                details.append({"field": field, "choices": enum or []})
            return ToolResult(ok=False, message="Valeurs requises manquantes : " + json.dumps(details, ensure_ascii=False), data={"missing_fields": details})
        for field, value in data.items():
            enum = schema.get("writable_fields", {}).get(field, {}).get("enum")
            if enum and value not in enum:
                return ToolResult(ok=False, message="Valeur invalide : " + json.dumps({"field": field, "value": value, "choices": enum}, ensure_ascii=False), data={"invalid_field": field, "choices": enum})
        return None

    def prepare_ndx_device_type(self, data: dict[str, Any]) -> ToolResult:
        model = str(data.get("model") or data.get("name") or "").strip()
        candidates = self.ndx.search(model)
        payload = self.ndx.build_payload(model)
        if payload is None:
            return ToolResult(ok=False, message=f"NDX retourne {len(candidates)} modèles. Sélectionne la référence exacte avant création.", data={"ndx_candidates": candidates})
        validated = NDXImportPayload.model_validate(payload)
        if validated.interface_count() == 0:
            return ToolResult(ok=False, message="Spec NDX incomplète : aucune interface publiée.", data={"reason": "empty_interface_templates"})
        return ToolResult(ok=True, message="Spec NDX exacte chargée.", data={"composite": {"type": "import_ndx_devicetype", "payload": payload}, "selected": self.ndx.exact(model, candidates)})

    def import_ndx_devicetype(self, arguments: dict[str, Any]) -> ToolResult:
        """Délègue l'exécution du DTO composite NDX validé."""
        raw_payload = dict(arguments.get("payload") or {}) if isinstance(arguments.get("payload"), dict) else {}
        return NDXCompositeImporter(self.find_existing_create, self.execute).run(raw_payload)

    def netbox_write(self, args: NetBoxWriteArgs) -> ToolResult:
        """Écriture universelle create/update/delete sur n'importe quel endpoint NetBox."""
        endpoint, app_name, plugin, actual = self._resolve_endpoint(args.app, args.endpoint)
        if endpoint is None:
            raise NetBoxChatError("Une ressource précise du plugin est obligatoire pour une écriture.")
        data = dict(args.data)

        if args.action == "create":
            record = endpoint.create(data)
            result = self._safe(record)
            message = f"Objet créé dans {args.app}/{args.endpoint}."
        else:
            object_id = data.pop("id", None)
            if object_id is None:
                raise NetBoxChatError(f"L’action {args.action} exige data.id.")
            record = endpoint.get(object_id)
            if record is None:
                raise ObjectNotFound(f"Objet ID {object_id} absent de {args.app}/{args.endpoint}.")
            if args.action == "update":
                record.update(data)
                refreshed = endpoint.get(object_id)
                result = self._safe(refreshed or record)
                message = f"Objet ID {object_id} mis à jour dans {args.app}/{args.endpoint}."
            elif args.action == "delete":
                record.delete()
                result = {"id": object_id, "deleted": True}
                message = f"Objet ID {object_id} supprimé de {args.app}/{args.endpoint}."
            else:
                raise NetBoxChatError(f"Action inconnue : {args.action}")
        return ToolResult(ok=True, message=message, data=result)

    def _deref(self, schema: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
        if "$ref" in schema:
            current: Any = spec
            for part in schema["$ref"].lstrip("#/").split("/"):
                current = current[part]
            return self._deref(current, spec)
        if "allOf" in schema:
            merged: dict[str, Any] = {"properties": {}, "required": []}
            for item in schema["allOf"]:
                resolved = self._deref(item, spec)
                merged["properties"].update(resolved.get("properties", {}))
                merged["required"].extend(resolved.get("required", []))
            return merged
        if schema.get("type") == "array" and isinstance(schema.get("items"), dict):
            return self._deref(schema["items"], spec)
        return schema

    def get_endpoint_schema(self, args: GetEndpointSchemaArgs) -> ToolResult:
        """Retourne le contrat OpenAPI live d'un endpoint NetBox."""
        endpoint, app_name, plugin, actual = self._resolve_endpoint(args.app, args.endpoint)
        if endpoint is None and plugin:
            endpoints = self._openapi_collections("plugins", plugin)
            return ToolResult(
                ok=True,
                message=f"{len(endpoints)} endpoint(s) disponibles pour le plugin {plugin}.",
                data={"plugin": plugin, "endpoints": endpoints},
            )

        spec = self.api.openapi()
        prefix = f"/api/{app_name}/" if app_name != "plugins" else f"/api/plugins/{plugin}/"
        path = f"{prefix}{actual}/"
        definition = spec.get("paths", {}).get(path)
        if not definition:
            available = self._openapi_collections(app_name, plugin)
            raise ObjectNotFound(
                f"Endpoint {path} absent du schéma OpenAPI. Endpoints disponibles : "
                + ", ".join(item["endpoint"] for item in available[:50])
            )

        filters = []
        get_op = definition.get("get", {})
        for parameter in get_op.get("parameters", []):
            filters.append({
                "name": parameter.get("name"),
                "required": bool(parameter.get("required")),
                "type": parameter.get("schema", {}).get("type"),
                "description": parameter.get("description", ""),
            })

        writable: dict[str, Any] = {}
        required: list[str] = []
        for method in ("post", "patch", "put"):
            operation = definition.get(method)
            if not operation:
                continue
            content = operation.get("requestBody", {}).get("content", {})
            media = content.get("application/json") or next(iter(content.values()), {})
            raw_schema = media.get("schema", {}) if isinstance(media, dict) else {}
            schema = self._deref(raw_schema, spec)
            if method == "post":
                required = sorted(set(required + list(schema.get("required", []))))
            for name, field in schema.get("properties", {}).items():
                resolved = self._deref(field, spec) if isinstance(field, dict) else {}
                writable[name] = {
                    "type": resolved.get("type"),
                    "format": resolved.get("format"),
                    "read_only": bool(resolved.get("readOnly")),
                    "nullable": bool(resolved.get("nullable")),
                    "enum": resolved.get("enum"),
                    "description": resolved.get("description", ""),
                }

        return ToolResult(
            ok=True,
            message=f"Schéma OpenAPI chargé pour {args.app}/{args.endpoint}.",
            data={
                "path": path,
                "methods": sorted(definition.keys()),
                "required_fields": required,
                "writable_fields": writable,
                "filters": filters,
            },
        )
