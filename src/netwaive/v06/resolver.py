from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any, Protocol

from .contracts import IntentResolution, ResolutionCertificate, ResolutionError, ResolutionKind, ResolvedRef


class ReadOnlyGateway(Protocol):
    def read(self, app: str, endpoint: str, *, filters: dict[str, Any]) -> list[dict[str, Any]]: ...


_ENDPOINTS = {
    ResolutionKind.SITE: ("dcim", "sites", "name"),
    ResolutionKind.MANUFACTURER: ("dcim", "manufacturers", "name"),
    ResolutionKind.DEVICE_TYPE: ("dcim", "device-types", "model"),
    ResolutionKind.MODULE_TYPE: ("dcim", "module-types", "model"),
    ResolutionKind.RACK: ("dcim", "racks", "name"),
    ResolutionKind.DEVICE: ("dcim", "devices", "name"),
}


class ReadOnlyResolver:
    """Résout des valeurs métier par lookup exact, sans write ni heuristique."""

    def __init__(self, gateway: ReadOnlyGateway):
        self.gateway = gateway
        self.resolver_id = str(uuid.uuid4())

    def resolve(self, kind: ResolutionKind, requested: str) -> ResolvedRef | ResolutionError:
        value = str(requested or "").strip()
        if not value:
            return ResolutionError(code="missing", message="Valeur de résolution manquante.", requested="")
        if kind == ResolutionKind.GENERIC:
            kind = ResolutionKind.MANUFACTURER
            value = "Generic"
        app, endpoint, field = _ENDPOINTS[kind]
        try:
            records = self.gateway.read(app, endpoint, filters={field: value})
        except Exception as exc:
            return ResolutionError(code="lookup_failed", message=f"Lookup NetBox échoué: {exc}", requested=value)
        records = [record for record in records if str(record.get(field) or "").strip() == value]
        refs = [self._ref(kind, app, endpoint, value, record) for record in records]
        refs = [ref for ref in refs if ref is not None]
        if not refs:
            return ResolutionError(code="not_found", message=f"Aucun objet {kind.value} exact pour {value}.", requested=value)
        if len(refs) > 1:
            return ResolutionError(code="ambiguous", message=f"Plusieurs objets {kind.value} correspondent à {value}.", requested=value, candidates=refs)
        return refs[0]

    def resolve_intent(
        self,
        request_text: str,
        *,
        model: str | None = None,
        name: str | None = None,
        refs: dict[ResolutionKind, str] | None = None,
        explicit_count: int | None = None,
        component_templates: dict[str, list[dict[str, Any]]] | None = None,
    ) -> IntentResolution:
        requested_refs = dict(refs or {})
        requested_refs.setdefault(ResolutionKind.MANUFACTURER, "Generic")
        resolved: dict[str, ResolvedRef] = {}
        errors: list[ResolutionError] = []
        for kind, value in requested_refs.items():
            result = self.resolve(kind, value)
            if isinstance(result, ResolutionError):
                errors.append(result)
            else:
                resolved[kind.value] = result
        intent = IntentResolution(request_text=request_text, model=model, name=name, refs=resolved, errors=errors, explicit_count=explicit_count, component_templates=component_templates or {})
        return self.certify(intent)

    def certify(self, intent: IntentResolution) -> IntentResolution:
        payload = json.dumps(intent.model_dump(mode="json", exclude={"certificate"}), sort_keys=True, ensure_ascii=False).encode()
        certificate = ResolutionCertificate(resolver_id=self.resolver_id, digest=hashlib.sha256(payload).hexdigest())
        return intent.model_copy(update={"certificate": certificate})

    def verify(self, intent: IntentResolution) -> bool:
        certificate = intent.certificate
        if certificate is None or certificate.resolver_id != self.resolver_id:
            return False
        payload = json.dumps(intent.model_dump(mode="json", exclude={"certificate"}), sort_keys=True, ensure_ascii=False).encode()
        return certificate.digest == hashlib.sha256(payload).hexdigest()

    @staticmethod
    def _ref(kind: ResolutionKind, app: str, endpoint: str, requested: str, record: dict[str, Any]) -> ResolvedRef | None:
        object_id = record.get("id")
        if isinstance(object_id, bool) or not isinstance(object_id, int) or object_id <= 0:
            return None
        display = str(record.get("display") or record.get("name") or record.get("model") or "").strip()
        if not display:
            return None
        return ResolvedRef(id=object_id, app=app, endpoint=endpoint, kind=kind, display=display, requested=requested)
