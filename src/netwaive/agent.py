from __future__ import annotations

import json
import re
from typing import Any

from openai import OpenAI
from pydantic import ValidationError

from .config import Settings
from .models import AgentResponse, PendingToolCall, ToolResult
from .prompt import SYSTEM_PROMPT
from .tools import NetBoxTools

REFERENCE_RE = re.compile(r"\$\{([^}]+)\}")


class NetBoxAgent:
    """Boucle agent bornée avec contexte, plan RW global et références symboliques."""

    def __init__(self, settings: Settings, tools: NetBoxTools | None = None, client: Any = None):
        self.settings = settings
        self.tools = tools or NetBoxTools(settings)
        self.client = client or OpenAI(
            api_key=settings.llm_api_key.get_secret_value(),
            base_url=settings.llm_base_url.rstrip("/"),
            timeout=settings.llm_timeout,
        )

    @staticmethod
    def _detect_language(user_message: str) -> str:
        text = user_message.lower()
        if re.search(r"[àâçéèêëîïôùûüÿœ]", text):
            return "fr"
        words = set(re.findall(r"[a-z']+", text))
        french_strong = {"crée", "créer", "ajoute", "supprime", "modifie", "rattache", "affecte", "merci"}
        english_strong = {"create", "add", "delete", "update", "attach", "assign", "please", "named", "list", "show", "which", "what"}
        if words & english_strong:
            return "en"
        if words & french_strong:
            return "fr"
        french = {"le", "la", "les", "un", "une", "des", "sur", "avec", "dans", "pour"}
        english = {"the", "a", "an", "on", "with", "in", "for", "at", "from"}
        return "en" if len(words & english) > len(words & french) else "fr"

    @staticmethod
    def _is_explicit_write_request(message: str) -> bool:
        return bool(re.search(
            r"\b(crée|créer|cree|create|ajoute|ajouter|supprime|supprimer|modifie|modifier|met à jour|update|delete|rattache|attache|affecte)\b",
            message,
            re.IGNORECASE,
        ))

    @staticmethod
    def _is_structured_plan(message: str) -> bool:
        return bool(re.search(
            r"(^|\n)\s*(?:[-*]|├──|└──|│|[\w-]+\s*:\s*(?:$|[^\n]+)|\{\s*\")",
            message,
            re.MULTILINE,
        ))

    @staticmethod
    def _is_transitional_response(content: str) -> bool:
        return bool(re.search(
            r"\b(compris|poursuis|continuer|automatiquement|prépare|je vais|enchaîne|vérifie|vérification|analyse|understood|proceed|continue|automatically|prepare|i will|verify|checking|analyzing)\b",
            content,
            re.IGNORECASE,
        ))

    @staticmethod
    def _messages(user_message: str, history: list[dict[str, str]] | None = None) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
        for item in (history or [])[-16:]:
            role = str(item.get("role") or "")
            text = str(item.get("text") or item.get("content") or "")[:4000]
            if role in {"user", "assistant"} and text:
                messages.append({"role": role, "content": text})
        if not messages or messages[-1].get("role") != "user" or messages[-1].get("content") != user_message:
            messages.append({"role": "user", "content": user_message})
        return messages

    def _parse_calls(self, calls: list[Any], results: list[ToolResult]):
        parsed: list[tuple[Any, dict[str, Any]]] = []
        for call in calls:
            try:
                raw = json.loads(call.function.arguments or "{}")
                model = self.tools.ARG_MODELS[call.function.name]
                arguments = model.model_validate(raw).model_dump(exclude_none=True)
            except (json.JSONDecodeError, KeyError, ValidationError) as exc:
                arguments = {}
                results.append(ToolResult(ok=False, message=f"Appel outil invalide : {exc}"))
            parsed.append((call, arguments))
        return parsed

    @staticmethod
    def _call_signature(call: PendingToolCall) -> str:
        return json.dumps({"name": call.name, "arguments": call.arguments}, sort_keys=True, ensure_ascii=False)

    @staticmethod
    def _collect_id_labels(outputs: dict[str, dict[str, Any]]) -> dict[int, set[str]]:
        labels: dict[int, set[str]] = {}

        def visit(value: Any) -> None:
            if isinstance(value, dict):
                object_id = value.get("id")
                label = value.get("display") or value.get("name") or value.get("address") or value.get("prefix")
                if isinstance(object_id, int) and label not in (None, ""):
                    labels.setdefault(object_id, set()).add(str(label))
                for item in value.values():
                    visit(item)
            elif isinstance(value, list):
                for item in value:
                    visit(item)

        visit(outputs)
        return labels

    @staticmethod
    def _reference_call_id(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        match = REFERENCE_RE.fullmatch(value)
        return match.group(1).split(".", 1)[0] if match else None

    @classmethod
    def _business_value(
        cls,
        value: Any,
        labels: dict[int, set[str]],
        planned_labels: dict[str, str],
    ) -> str:
        reference = cls._reference_call_id(value)
        if reference:
            return planned_labels.get(reference, "__planned_reference__")
        if isinstance(value, bool):
            return "oui" if value else "non"
        if isinstance(value, int) and len(labels.get(value, set())) == 1:
            return next(iter(labels[value]))
        if isinstance(value, int):
            return ""
        if isinstance(value, str):
            return value
        if value is None:
            return ""
        return str(value)

    @staticmethod
    def _resource_label(endpoint: str, data: dict[str, Any] | None = None, language: str = "fr") -> str:
        labels = {
            "manufacturers": ("Fabricant", "Manufacturer"),
            "device-types": ("Type d’équipement", "Device Type"),
            "devices": ("Équipement", "Device"),
            "sites": ("Site", "Site"),
            "ip-addresses": ("Adresse IP", "IP Address"),
            "prefixes": ("Préfixe", "Prefix"),
            "vlans": ("VLAN", "VLAN"),
            "interfaces": ("Interface", "Interface"),
            "cables": ("Câble", "Cable"),
            "vlan-groups": ("Groupe de VLANs", "VLAN Group"),
            "custom-fields": ("Champ personnalisé", "Custom Field"),
            "platforms": ("Plateforme", "Platform"),
            "device-roles": ("Rôle d’équipement", "Device Role"),
        }
        normalized = str(endpoint or "").replace("_", "-").lower()
        if normalized == "interfaces" and str((data or {}).get("type") or "").lower() == "lag":
            return "LAG"
        pair = labels.get(normalized, ("Objet NetBox", "NetBox Object"))
        return pair[1] if language == "en" else pair[0]

    @classmethod
    def _planned_business_labels(
        cls,
        pending: list[PendingToolCall],
        labels: dict[int, set[str]],
    ) -> dict[str, str]:
        planned: dict[str, str] = {}
        for call in pending:
            data = call.arguments.get("data") if isinstance(call.arguments.get("data"), dict) else {}
            endpoint = str(call.arguments.get("endpoint") or "").replace("_", "-").lower()
            if endpoint == "vlans":
                name = data.get("name")
                vid = data.get("vid")
                label = str(name or (f"VLAN {vid}" if vid is not None else "VLAN"))
            elif endpoint == "prefixes":
                label = str(data.get("prefix") or "Préfixe")
            elif endpoint == "ip-addresses":
                label = str(data.get("address") or "Adresse IP")
            else:
                label = str(data.get("name") or data.get("model") or data.get("display") or data.get("address") or cls._resource_label(endpoint, data))
            planned[call.id] = cls._business_value(label, labels, planned)
        return planned

    @staticmethod
    def _planned_relation_labels(pending: list[PendingToolCall]) -> dict[str, str]:
        relations: dict[str, str] = {}
        for call in pending:
            data = call.arguments.get("data") if isinstance(call.arguments.get("data"), dict) else {}
            endpoint = str(call.arguments.get("endpoint") or "").replace("_", "-").lower()
            if endpoint == "sites" and data.get("name"):
                relations["site"] = str(data["name"])
            elif endpoint == "vlans":
                relations["vlan"] = str(data.get("name") or f"VLAN {data.get('vid')}")
            elif endpoint == "devices" and data.get("name"):
                relations["device"] = str(data["name"])
            elif endpoint == "interfaces" and str(data.get("type") or "").lower() == "lag":
                relations["lag"] = str(data.get("name") or "LAG planifié")
            elif endpoint == "prefixes" and data.get("prefix"):
                relations["prefix"] = str(data["prefix"])
        return relations

    @classmethod
    def _relation_suffix(
        cls,
        data: dict[str, Any],
        labels: dict[int, set[str]],
        planned_labels: dict[str, str],
        planned_relations: dict[str, str],
        *,
        language: str,
    ) -> str:
        relations: list[str] = []
        relation_names = {
            "vlan": ("VLAN", "VLAN"),
            "site": ("site", "site"),
            "device": ("équipement", "device"),
            "lag": ("LAG", "LAG"),
            "role": ("rôle", "role"),
            "platform": ("plateforme", "platform"),
        }
        for key, (fr_name, en_name) in relation_names.items():
            if key not in data:
                continue
            value = cls._business_value(data[key], labels, planned_labels)
            if value == "__planned_reference__":
                value = planned_relations.get(key, {"site": "Site", "vlan": "VLAN", "device": "Équipement", "lag": "LAG"}.get(key, "Objet NetBox"))
            if not value:
                continue
            if language == "fr":
                if key in {"site", "vlan", "lag"}:
                    relations.append(f"au {fr_name} {value}")
                elif key == "device":
                    relations.append(f"à l’équipement {value}")
                elif key == "platform":
                    relations.append(f"avec la plateforme {value}")
                else:
                    relations.append(f"avec le {fr_name} {value}")
            else:
                relations.append(f"to {en_name} {value}")
        if not relations:
            return ""
        prefix = "Rattaché " if language == "fr" else "Attached "
        return " (" + prefix + " et ".join(relations) + ")" if language == "fr" else " (" + prefix + " and ".join(relations) + ")"

    @classmethod
    def _pending_message(cls, pending: list[PendingToolCall], outputs: dict[str, dict[str, Any]], language: str) -> str:
        """Generic confirmation renderer: endpoint/action/payload only, no resource-specific branches."""
        english = language == "en"
        lines = ["Pending changes awaiting your validation:" if english else "Modifications en attente de votre validation :"]
        for call in pending:
            if call.name == "import_ndx_object":
                payload = call.arguments.get("payload", {})
                parent = payload.get("parent", {}) if isinstance(payload, dict) else {}
                comp = payload.get("component_templates", {}) if isinstance(payload, dict) else {}
                label = "ModuleType" if payload.get("object_type") == "module-type" else "DeviceType"
                lines.append(f"• Import NDX : {label} '{payload.get('manufacturer')} {parent.get('model')}' (1 {label}, {len(comp.get('interfaces', []))} interfaces, {len(comp.get('power-ports', []))} ports alimentation, {len(comp.get('console-ports', []))} ports console)")
                continue
            args = call.arguments
            data = args.get("data") if isinstance(args.get("data"), dict) else {}
            action = str(args.get("action") or "create").lower()
            endpoint = str(args.get("endpoint") or "object").replace("_", "-")
            label = endpoint.replace("-", " ")
            identity = next((str(data[key]) for key in ("name", "slug", "prefix", "address", "model", "id") if data.get(key) not in (None, "")), "")
            verb = {"create": "Create", "update": "Update", "delete": "Delete"}.get(action, action.title()) if english else {"create": "Création", "update": "Mise à jour", "delete": "Suppression"}.get(action, action.title())
            display = f"{label} '{identity}'" if identity else label
            lines.append(f"• {verb}: {display}")
        lines.append("\nDo you approve these operations?" if english else "\nConfirmez-vous l’exécution de ces opérations ?")
        return "\n".join(lines)


    @staticmethod
    def _target_key(arguments: dict[str, Any]) -> tuple[str, str]:
        return (
            str(arguments.get("app") or "").strip().lower().replace("_", "-"),
            str(arguments.get("endpoint") or "").strip().lower().replace("_", "-"),
        )

    @staticmethod
    def _collect_observed_ids(value: Any) -> set[int]:
        observed: set[int] = set()
        if isinstance(value, dict):
            if isinstance(value.get("id"), int):
                observed.add(value["id"])
            for item in value.values():
                observed.update(NetBoxAgent._collect_observed_ids(item))
        elif isinstance(value, list):
            for item in value:
                observed.update(NetBoxAgent._collect_observed_ids(item))
        return observed

    @classmethod
    def _write_guard(
        cls,
        arguments: dict[str, Any],
        read_targets: set[tuple[str, str]],
        observed_ids: set[int],
        language: str,
        observed_records: dict[tuple[str, str], list[dict[str, Any]]] | None = None,
    ) -> ToolResult | None:
        target = cls._target_key(arguments)
        if target not in read_targets:
            message = (
                f"Contrôle RO obligatoire : lis d'abord {target[0]}/{target[1]} avec netbox_read avant de préparer cette mutation."
                if language == "fr"
                else f"Mandatory read-only check: call netbox_read on {target[0]}/{target[1]} before planning this mutation."
            )
            return ToolResult(ok=False, message=message, data={"strict_ro_check_required": True})
        data = arguments.get("data") if isinstance(arguments.get("data"), dict) else {}
        action = str(arguments.get("action") or "").lower()
        if action == "create":
            records = (observed_records or {}).get(target, [])
            identity_fields = ("name", "prefix", "address", "slug", "vid")
            for record in records:
                if not isinstance(record, dict):
                    continue
                for field in identity_fields:
                    requested = data.get(field)
                    observed = record.get(field)
                    if requested not in (None, "") and observed not in (None, "") and str(requested).casefold() == str(observed).casefold():
                        resource = cls._resource_label(target[1], data, language)
                        message = (
                            f"{resource} already exists in NetBox: no additional creation is required."
                            if language == "en"
                            else f"{resource} déjà présent dans NetBox : aucune création supplémentaire n’est nécessaire."
                        )
                        return ToolResult(
                            ok=False,
                            message=message,
                            data={"existing_object": True, "resource": resource},
                        )
        object_id = data.get("id") if isinstance(data, dict) else None
        if action in {"update", "delete"} and isinstance(object_id, int) and object_id not in observed_ids:
            message = (
                "ID de cible non observé pendant les lectures live ; mutation refusée."
                if language == "fr"
                else "Target ID was not observed in live reads; mutation rejected."
            )
            return ToolResult(ok=False, message=message, data={"unobserved_id": True})
        return None

    @staticmethod
    def _planned_result(call: PendingToolCall) -> ToolResult:
        symbolic_id = f"${{{call.id}.data.id}}"
        return ToolResult(
            ok=True,
            message=f"Mutation planifiée : {call.name}. Continue le plan complet avant confirmation.",
            data={"planned": True, "call_id": call.id, "id": symbolic_id},
        )

    @staticmethod
    def _resolve_reference(expression: str, outputs: dict[str, dict[str, Any]]) -> Any:
        parts = expression.split(".")
        if not parts:
            raise ValueError(f"Référence symbolique non résolue : ${{{expression}}}")
        key = parts[0]
        if key not in outputs:
            alias = re.fullmatch(r"call_(\d+)", key)
            if alias:
                index = int(alias.group(1)) - 1
                keys = list(outputs)
                if 0 <= index < len(keys):
                    key = keys[index]
        if key not in outputs and key.startswith("call_") and outputs:
            # Some providers regenerate opaque call IDs between planning and execution.
            # The dependency ordering guarantees the immediately preceding successful output is the parent.
            key = next(reversed(outputs))
        if key not in outputs:
            raise ValueError(f"Référence symbolique non résolue : ${{{expression}}}")
        current: Any = outputs[key]
        for part in parts[1:]:
            if isinstance(current, dict) and part in current:
                current = current[part]
                continue
            if part == "id" and isinstance(current, dict) and isinstance(current.get("data"), dict) and "id" in current["data"]:
                current = current["data"]["id"]
                continue
            raise ValueError(f"Référence symbolique non résolue : ${{{expression}}}")
        return current

    @classmethod
    def _resolve_references(cls, value: Any, outputs: dict[str, dict[str, Any]]) -> Any:
        if isinstance(value, dict):
            return {key: cls._resolve_references(item, outputs) for key, item in value.items()}
        if isinstance(value, list):
            return [cls._resolve_references(item, outputs) for item in value]
        if not isinstance(value, str) or "${" not in value:
            return value
        exact = REFERENCE_RE.fullmatch(value)
        if exact:
            return cls._resolve_reference(exact.group(1), outputs)
        return REFERENCE_RE.sub(lambda match: str(cls._resolve_reference(match.group(1), outputs)), value)

    @classmethod
    def _resolve_available_references(cls, value: Any, outputs: dict[str, dict[str, Any]]) -> Any:
        if isinstance(value, dict):
            return {key: cls._resolve_available_references(item, outputs) for key, item in value.items()}
        if isinstance(value, list):
            return [cls._resolve_available_references(item, outputs) for item in value]
        if not isinstance(value, str) or "${" not in value:
            return value
        exact = REFERENCE_RE.fullmatch(value)
        if exact and exact.group(1).split(".")[0] in outputs:
            return cls._resolve_reference(exact.group(1), outputs)
        return REFERENCE_RE.sub(
            lambda match: str(cls._resolve_reference(match.group(1), outputs))
            if match.group(1).split(".")[0] in outputs else match.group(0),
            value,
        )

    def _loop(
        self,
        messages: list[dict[str, Any]],
        *,
        confirm_write: bool,
        results: list[ToolResult] | None = None,
        planned: list[PendingToolCall] | None = None,
        language: str = "fr",
        require_live_plan: bool = False,
    ) -> AgentResponse:
        collected = list(results or [])
        write_plan = list(planned or [])
        tool_outputs: dict[str, dict[str, Any]] = {}
        read_targets: set[tuple[str, str]] = set()
        observed_ids: set[int] = set()
        observed_records: dict[tuple[str, str], list[dict[str, Any]]] = {}
        signatures = {self._call_signature(call) for call in write_plan}
        missing_recovery_used = False
        plan_completion_repair_used = False
        plan_repair_attempts = 0

        for _ in range(self.settings.max_agent_turns):
            response = self.client.chat.completions.create(
                model=self.settings.llm_model,
                messages=messages,
                tools=self.tools.tool_schemas(),
                tool_choice="auto",
            )
            assistant = response.choices[0].message
            calls = list(assistant.tool_calls or [])
            if not calls:
                content = assistant.content or ""
                false_confirmation = bool(re.search(r"modifications? en attente|confirmez-vous|confirmation|pending changes|do you approve", content, re.IGNORECASE))
                transitional_response = self._is_transitional_response(content)
                needs_tool_chain = require_live_plan and not write_plan and (
                    not collected or false_confirmation or transitional_response
                )
                if needs_tool_chain and plan_repair_attempts < 3:
                    messages.append(assistant.model_dump(exclude_none=True))
                    messages.append({
                        "role": "user",
                        "content": (
                            "Demande de mutation claire : ne réponds jamais par un accusé de réception ou une transition textuelle. "
                            "Dans cette même boucle, appelle immédiatement les netbox_read nécessaires, puis prépare les netbox_write si l’action est requise. "
                            "Le runtime affichera lui-même la carte de confirmation uniquement lorsqu’un pending_write réel existe. "
                            "Si un prérequis manque réellement, pose une question précise ; sinon poursuis avec les outils maintenant."
                        ),
                    })
                    plan_repair_attempts += 1
                    continue
                if needs_tool_chain:
                    message = (
                        "The agent did not produce the required NetBox tool chain; no change was planned or executed."
                        if language == "en"
                        else "L’agent n’a pas produit le chaînage d’outils NetBox requis ; aucune modification n’a été planifiée ni exécutée."
                    )
                    return AgentResponse(message=message, tool_results=collected)
                last_failure = next((item for item in reversed(collected) if not item.ok), None)
                missing_dependency = bool(
                    last_failure
                    and re.search(r"aucun|absent|n[’']existe pas|not found|404", last_failure.message, re.IGNORECASE)
                )
                creation_question = bool(
                    "?" in content and re.search(r"créer|création|create", content, re.IGNORECASE)
                )
                if missing_dependency and not write_plan and not missing_recovery_used and not creation_question:
                    messages.append(assistant.model_dump(exclude_none=True))
                    messages.append({
                        "role": "user",
                        "content": (
                            "Un prérequis est absent. Ne t'arrête pas à ce constat : "
                            "si les paramètres sont disponibles, ajoute sa création au plan global ; "
                            "sinon pose immédiatement une seule question précise proposant sa création."
                        ),
                    })
                    missing_recovery_used = True
                    continue
                if write_plan:
                    if require_live_plan and not plan_completion_repair_used:
                        messages.append(assistant.model_dump(exclude_none=True))
                        messages.append({
                            "role": "user",
                            "content": (
                                "Audit interne obligatoire : le plan courant contient des écritures mais doit couvrir l’objectif final initial. "
                                "Continue maintenant les netbox_read et netbox_write nécessaires pour inclure chaque dépendance manquante et l’objet final. "
                                "Ne retourne aucun texte utilisateur avant le plan complet ; le runtime affichera la carte après ce tour."
                            ),
                        })
                        plan_completion_repair_used = True
                        continue
                    write_plan, sanitation_errors = self._sanitize_plan(write_plan)
                    if sanitation_errors:
                        return AgentResponse(message="Plan refusé avant confirmation : " + " ".join(sanitation_errors), tool_results=collected)
                    return AgentResponse(
                        message=self._pending_message(write_plan, tool_outputs, language),
                        pending_confirmation=write_plan,
                        tool_results=collected,
                    )
                return AgentResponse(
                    message=assistant.content or "Le modèle n’a retourné aucune réponse.",
                    tool_results=collected,
                )

            parsed = self._parse_calls(calls, collected)
            messages.append(assistant.model_dump(exclude_none=True))
            for call, arguments in parsed:
                if call.function.name in self.tools.MUTATING_TOOLS and not confirm_write:
                    ndx_preparer = getattr(self.tools, "prepare_ndx_object", None)
                    endpoint_name = str(arguments.get("endpoint") or "").replace("_", "-").lower()
                    object_type = {"device-types": "device-type", "device-type": "device-type", "module-types": "module-type", "module-type": "module-type"}.get(endpoint_name)
                    if call.function.name == "netbox_write" and callable(ndx_preparer) and object_type and str(arguments.get("action") or "") == "create" and str(arguments.get("app") or "").lower() == "dcim":
                        prepared = ndx_preparer(arguments.get("data") or {}, object_type)
                        if prepared.ok and isinstance(prepared.data, dict) and prepared.data.get("composite"):
                            composite = prepared.data["composite"]
                            pending_call = PendingToolCall(id="ndx-import", name="import_ndx_object", arguments=composite)
                            write_plan.append(pending_call)
                            signatures.add(self._call_signature(pending_call))
                            result = self._planned_result(pending_call)
                            collected.append(result)
                            messages.append({"role": "tool", "tool_call_id": call.id, "content": result.model_dump_json()})
                            continue
                        result = prepared
                        collected.append(result)
                        messages.append({"role": "tool", "tool_call_id": call.id, "content": result.model_dump_json()})
                        continue
                    arguments = self._resolve_available_references(arguments, tool_outputs)
                    collision = None
                    collision_checker = getattr(self.tools, "preflight_termination_collisions", None)
                    if callable(collision_checker):
                        collision = collision_checker(arguments)
                    if collision is not None and not collision.ok:
                        result = collision
                        collected.append(result)
                    else:
                        preflight = None
                        validator = getattr(self.tools, "validate_write_payload", None)
                        if callable(validator):
                            preflight = validator(arguments)
                        if preflight is not None and not preflight.ok:
                            result = preflight
                            collected.append(result)
                        else:
                            guard = self._write_guard(arguments, read_targets, observed_ids, language, observed_records)
                            if guard is not None:
                                result = guard
                                collected.append(result)
                            else:
                                pending_call = PendingToolCall(id=call.id, name=call.function.name, arguments=arguments)
                                signature = self._call_signature(pending_call)
                                if signature not in signatures:
                                    write_plan.append(pending_call)
                                    signatures.add(signature)
                                result = self._planned_result(pending_call)
                else:
                    result = self.tools.execute(call.function.name, arguments)
                    collected.append(result)
                    tool_outputs[call.id] = result.model_dump()
                    if call.function.name == "netbox_read" and str(arguments.get("app") or "").strip().lower() == "ndx" and isinstance(result.data, dict) and result.data.get("parent"):
                        payload = {key: result.data.get(key) for key in ("object_type", "manufacturer", "parent", "component_templates")}
                        pending_call = PendingToolCall(id="ndx-import", name="import_ndx_object", arguments={"type": "import_ndx_object", "payload": payload})
                        signature = self._call_signature(pending_call)
                        if signature not in signatures:
                            write_plan.append(pending_call)
                            signatures.add(signature)
                    if call.function.name == "netbox_read":
                        read_targets.add(self._target_key(arguments))
                        if result.ok:
                            observed_ids.update(self._collect_observed_ids(result.data))
                            records = result.data if isinstance(result.data, list) else [result.data]
                            observed_records.setdefault(self._target_key(arguments), []).extend(
                                record for record in records if isinstance(record, dict)
                            )
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": result.model_dump_json(),
                })

        if write_plan:
            write_plan, sanitation_errors = self._sanitize_plan(write_plan)
            if sanitation_errors:
                return AgentResponse(message="Plan refusé avant confirmation : " + " ".join(sanitation_errors), tool_results=collected)
            return AgentResponse(
                message=self._pending_message(write_plan, tool_outputs, language),
                pending_confirmation=write_plan,
                tool_results=collected,
            )
        return AgentResponse(
            message="La limite de tours de l’agent a été atteinte sans résultat final.",
            tool_results=collected,
        )

    def run(
        self,
        user_message: str,
        *,
        confirm_write: bool = False,
        history: list[dict[str, str]] | None = None,
    ) -> AgentResponse:
        messages = self._messages(user_message, history)
        return self._loop(
            messages,
            confirm_write=confirm_write,
            language=self._detect_language(user_message),
            require_live_plan=self._is_explicit_write_request(user_message),
        )

    @staticmethod
    def _reference_dependencies(value: Any) -> set[str]:
        if isinstance(value, dict):
            return set().union(*(NetBoxAgent._reference_dependencies(item) for item in value.values()))
        if isinstance(value, list):
            return set().union(*(NetBoxAgent._reference_dependencies(item) for item in value))
        if isinstance(value, str):
            return {match.group(1).split(".", 1)[0] for match in REFERENCE_RE.finditer(value)}
        return set()

    @classmethod
    def _sanitize_plan(cls, pending: list[PendingToolCall]) -> tuple[list[PendingToolCall], list[str]]:
        """Deduplicate exact calls and reject unresolved/unknown symbolic references before confirmation."""
        unique: list[PendingToolCall] = []
        seen: set[str] = set()
        for call in pending:
            signature = cls._call_signature(call)
            if signature not in seen:
                seen.add(signature)
                unique.append(call)
        call_ids = {call.id for call in unique}
        errors: list[str] = []
        for call in unique:
            rendered = json.dumps(call.arguments, ensure_ascii=False)
            if "${" in rendered:
                refs = cls._reference_dependencies(call.arguments)
                unknown = refs - call_ids
                # Opaque call_* IDs may be emitted by providers before their final call IDs are materialized.
                # Let the universal runtime resolver handle them; reject only malformed/non-tool variables here.
                if not refs or any(not ref.startswith("call_") for ref in unknown):
                    errors.append(f"La référence de l’étape « {call.id} » est invalide ou inconnue.")
        return cls._order_pending(unique), errors

    @classmethod
    def _order_pending(cls, pending: list[PendingToolCall]) -> list[PendingToolCall]:
        """Stable topological ordering: parent creations always precede dependent calls."""
        by_id = {call.id: call for call in pending}
        unresolved = list(pending)
        ordered: list[PendingToolCall] = []
        resolved: set[str] = set()
        while unresolved:
            ready = [call for call in unresolved if cls._reference_dependencies(call.arguments).intersection(by_id).issubset(resolved)]
            if not ready:
                ordered.extend(unresolved)
                break
            for call in ready:
                ordered.append(call)
                resolved.add(call.id)
                unresolved.remove(call)
        return ordered

    def confirm(
        self,
        user_message: str,
        pending: list[PendingToolCall],
        *,
        history: list[dict[str, str]] | None = None,
    ) -> AgentResponse:
        """Exécute exactement le lot approuvé et clôt immédiatement ce cycle."""
        ordered, sanitation_errors = self._sanitize_plan(pending)
        if sanitation_errors:
            return AgentResponse(message="Plan refusé avant exécution : " + " ".join(sanitation_errors), tool_results=[])
        results: list[ToolResult] = []
        outputs: dict[str, dict[str, Any]] = {}
        failed_call: PendingToolCall | None = None
        for call in ordered:
            try:
                arguments = self._resolve_references(call.arguments, outputs)
            except ValueError as exc:
                results.append(ToolResult(ok=False, message=str(exc)))
                failed_call = call
                break
            existing = None
            if call.name == "netbox_write" and str(arguments.get("action") or "").lower() == "create":
                try:
                    existing = self.tools.find_existing_create(arguments)
                except Exception:
                    existing = None
            if call.name == "import_ndx_object":
                result = self.tools.import_ndx_object(arguments)
            else:
                result = existing or self.tools.execute(call.name, arguments)
            results.append(result)
            outputs[call.id] = result.model_dump()
            if not result.ok:
                failed_call = call
                break

        language = self._detect_language(user_message)
        if results and all(result.ok for result in results) and len(results) == len(ordered):
            message = (
                f"{len(results)} approved operation(s) were executed successfully. The requested configuration is now in place."
                if language == "en"
                else f"Les {len(results)} opération(s) validée(s) ont été exécutées avec succès. La configuration demandée est maintenant en place."
            )
        else:
            completed = sum(1 for result in results if result.ok)
            failed_id = failed_call.id if failed_call else ""
            blocked = {failed_id} if failed_id else set()
            tail = ordered[len(results):]
            runnable: list[PendingToolCall] = []
            for call in tail:
                dependencies = self._reference_dependencies(call.arguments)
                if dependencies & blocked:
                    blocked.add(call.id)
                    continue
                try:
                    resolved = self._resolve_references(call.arguments, outputs)
                except ValueError:
                    blocked.add(call.id)
                    continue
                runnable.append(PendingToolCall(id=call.id, name=call.name, arguments=resolved))
            failure = results[-1].message if results and not results[-1].ok else "unknown error"
            message = (
                f"Execution stopped after {completed}/{len(ordered)} operations. NetBox error: {failure}. "
                f"{len(runnable)} independent operation(s) can still be completed."
                if language == "en"
                else f"Exécution interrompue après {completed}/{len(ordered)} opérations. Erreur NetBox : {failure}. "
                f"{len(runnable)} opération(s) indépendante(s) peuvent encore être finalisées."
            )
            return AgentResponse(message=message, tool_results=results, pending_confirmation=runnable)
        return AgentResponse(message=message, tool_results=results)
