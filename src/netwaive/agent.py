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
        """NetWAIve est volontairement francophone côté interface et confirmations."""
        return "fr"

    @staticmethod
    def _is_explicit_write_request(message: str) -> bool:
        return bool(re.search(
            r"\b(crée|créer|cree|create|ajoute|ajouter|supprime|supprimer|modifie|modifier|met à jour|update|delete|rattache|attache|affecte)\b",
            message,
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
    def _resource_label(endpoint: str, data: dict[str, Any] | None = None) -> str:
        labels = {
            "manufacturers": "Fabricant",
            "device-types": "Type d’équipement",
            "devices": "Équipement",
            "sites": "Site",
            "ip-addresses": "Adresse IP",
            "prefixes": "Préfixe",
            "vlans": "VLAN",
            "interfaces": "Interface",
            "cables": "Câble",
            "vlan-groups": "Groupe de VLANs",
            "custom-fields": "Champ personnalisé",
            "platforms": "Plateforme",
            "device-roles": "Rôle d’équipement",
        }
        normalized = str(endpoint or "").replace("_", "-").lower()
        if normalized == "interfaces" and str((data or {}).get("type") or "").lower() == "lag":
            return "LAG"
        return labels.get(normalized, "Objet NetBox")

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
        labels = cls._collect_id_labels(outputs)
        planned_labels = cls._planned_business_labels(pending, labels)
        planned_relations = cls._planned_relation_labels(pending)
        lines = ["Modifications en attente de votre validation :"]

        for call in pending:
            args = call.arguments
            data = dict(args.get("data")) if isinstance(args.get("data"), dict) else {}
            endpoint = str(args.get("endpoint") or "").replace("_", "-").lower()
            action = str(args.get("action") or "create").lower()
            resource = cls._resource_label(endpoint, data)
            relation = cls._relation_suffix(data, labels, planned_labels, planned_relations, language="fr")
            name = cls._business_value(data.get("name") or data.get("model"), labels, planned_labels)
            target = cls._business_value(data.get("id"), labels, planned_labels)
            display = target or name or planned_labels.get(call.id, resource)

            if action == "delete":
                if endpoint == "ip-addresses":
                    address = cls._business_value(data.get("address"), labels, planned_labels) or display
                    line = f"• Suppression de l’adresse IP : '{address}'"
                else:
                    line = f"• Suppression : {resource} '{display}'"
            elif endpoint == "sites" and action == "create":
                line = f"• Création du site : '{name or resource}'"
            elif endpoint == "devices" and action == "create":
                if re.match(r"(?i)^(srv|server)", name or ""):
                    line = f"• Création du serveur : '{name}'{relation}"
                else:
                    line = f"• Création de l’équipement : '{name or resource}'{relation}"
            elif endpoint == "interfaces" and action == "create" and str(data.get("type") or "").lower() == "lag":
                line = f"• Création du LAG : '{name or resource}'{relation}"
            elif endpoint == "vlans" and action == "create":
                vid = data.get("vid")
                line = f"• Création du VLAN : VID {vid} — Nom '{name or resource}'{relation}"
            elif endpoint == "prefixes" and action == "create":
                prefix = cls._business_value(data.get("prefix"), labels, planned_labels) or resource
                line = f"• Création du préfixe : {prefix}{relation}"
                if data.get("is_pool") is True:
                    line += " — Utilisé comme pool d’adresses"
            elif endpoint == "ip-addresses":
                address = cls._business_value(data.get("address"), labels, planned_labels)
                destination = cls._business_value(
                    data.get("device") or data.get("assigned_object_id") or data.get("interface"),
                    labels,
                    planned_labels,
                )
                if destination == "__planned_reference__":
                    destination = planned_relations.get("device", "la cible prévue")
                source_prefix = planned_relations.get("prefix") or next((value for value in planned_labels.values() if "/" in value), "le préfixe prévu")
                if not address or address == "__planned_reference__" or address == "Adresse IP":
                    line = f"• Attribution d’IP : La première IP disponible dans {source_prefix} sera attribuée"
                else:
                    line = f"• Attribution d’IP : L’adresse {address} sera attribuée"
                if destination:
                    line += f" à {destination}"
                line += " dès validation."
            elif endpoint == "interfaces" and action == "update" and "lag" in data:
                interface = target or name or "Interface"
                lag = cls._business_value(data.get("lag"), labels, planned_labels)
                if lag == "__planned_reference__":
                    lag = planned_relations.get("lag", "LAG")
                line = f"• Rattachement de l’interface '{interface}' au LAG '{lag}'"
            elif endpoint == "prefixes" and action == "update" and "vlan" in data:
                prefix = target or cls._business_value(data.get("prefix"), labels, planned_labels) or "Préfixe"
                vlan = cls._business_value(data.get("vlan"), labels, planned_labels)
                if vlan == "__planned_reference__":
                    vlan = planned_relations.get("vlan", "VLAN")
                line = f"• Rattachement du préfixe {prefix} au VLAN '{vlan}'{relation}"
            elif action == "update":
                line = f"• Mise à jour : {resource} '{display}'{relation}"
            else:
                line = f"• Création : {resource} '{display}'{relation}"
            lines.append(line)

        lines.append("\nConfirmez-vous l’exécution de ces opérations ?")
        return "\n".join(lines).replace("__planned_reference__", "Objet NetBox")

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
                        resource = cls._resource_label(target[1], data)
                        return ToolResult(
                            ok=False,
                            message=f"{resource} déjà présent dans NetBox : aucune création supplémentaire n’est nécessaire.",
                            data={"existing_object": True, "resource": resource},
                        )
        if target == ("dcim", "interfaces") and action == "create":
            name = str(data.get("name") or "")
            interface_type = str(data.get("type") or "").lower()
            ip_derived_name = re.fullmatch(r"(?:l3[-_.]?)?\d{1,3}(?:\.\d{1,3}){3}", name, re.IGNORECASE)
            if interface_type in {"virtual", "l3"} and ip_derived_name:
                return ToolResult(
                    ok=False,
                    message=(
                        "Nom d’interface L3 refusé : une adresse IP ne doit pas servir de nom. "
                        "Utilise une SVI telle que Vlan71 ou l’interface physique explicitement demandée."
                    ),
                    data={"invalid_l3_interface_name": True},
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
        if not parts or parts[0] not in outputs:
            raise ValueError(f"Référence symbolique non résolue : ${{{expression}}}")
        current: Any = outputs[parts[0]]
        for part in parts[1:]:
            if not isinstance(current, dict) or part not in current:
                raise ValueError(f"Référence symbolique non résolue : ${{{expression}}}")
            current = current[part]
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
        plan_repair_used = False

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
                false_confirmation = bool(re.search(r"modifications? en attente|confirmez-vous|confirmation", content, re.IGNORECASE))
                if require_live_plan and not write_plan and not plan_repair_used and (not collected or false_confirmation):
                    messages.append(assistant.model_dump(exclude_none=True))
                    messages.append({
                        "role": "user",
                        "content": (
                            "Demande de mutation détectée : ne produis pas une confirmation en prose sans plan réel. "
                            "Exécute d’abord les netbox_read nécessaires. Si les objets existent déjà, réponds qu’ils sont tous en place ; "
                            "sinon prépare les netbox_write et laisse le runtime rendre la confirmation."
                        ),
                    })
                    plan_repair_used = True
                    continue
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
                    arguments = self._resolve_available_references(arguments, tool_outputs)
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
        return self._loop(
            self._messages(user_message, history),
            confirm_write=confirm_write,
            language=self._detect_language(user_message),
            require_live_plan=self._is_explicit_write_request(user_message),
        )

    def confirm(
        self,
        user_message: str,
        pending: list[PendingToolCall],
        *,
        history: list[dict[str, str]] | None = None,
    ) -> AgentResponse:
        """Exécute exactement le lot approuvé et clôt immédiatement ce cycle."""
        results: list[ToolResult] = []
        outputs: dict[str, dict[str, Any]] = {}
        for call in pending:
            try:
                arguments = self._resolve_references(call.arguments, outputs)
            except ValueError as exc:
                results.append(ToolResult(ok=False, message=str(exc)))
                break
            result = self.tools.execute(call.name, arguments)
            results.append(result)
            outputs[call.id] = result.model_dump()
            if not result.ok:
                break

        if results and all(result.ok for result in results) and len(results) == len(pending):
            message = (
                f"Les {len(results)} opération(s) validée(s) ont été exécutées avec succès. "
                "La configuration demandée est maintenant en place."
            )
        else:
            completed = sum(1 for result in results if result.ok)
            message = (
                f"Exécution interrompue : {completed} opération(s) sur {len(pending)} ont réussi. "
                "Aucune opération restante n’a été relancée automatiquement."
            )
        return AgentResponse(message=message, tool_results=results)
