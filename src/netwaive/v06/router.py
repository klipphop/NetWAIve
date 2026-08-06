from __future__ import annotations

import re
from typing import Any, Protocol

from .contracts import IntentResolution, RouteDecision, RouteKind


class NDXReadOnly(Protocol):
    def search(self, model: str) -> list[dict[str, Any]]: ...


class IntentRouter:
    READ_WORDS = re.compile(r"\b(?:liste|list|lister|affiche|afficher|montre|montrer|cherche|chercher|quels?|quelles?|tous|toutes|show|list)\b", re.IGNORECASE)
    WRITE_WORDS = re.compile(r"\b(?:crée|creer|créer|create|ajoute|ajouter|add|modifie|modifier|supprime|supprimer|delete|change|changer)\b", re.IGNORECASE)

    @classmethod
    def is_read_only(cls, request_text: str) -> bool:
        return bool(cls.READ_WORDS.search(request_text)) and not bool(cls.WRITE_WORDS.search(request_text))

    """Choisit NDX ou custom sans mutation et sans recherche floue locale."""

    def __init__(self, ndx: NDXReadOnly):
        self.ndx = ndx

    def route(self, intent: IntentResolution) -> RouteDecision:
        if not intent.resolved:
            return RouteDecision(kind=RouteKind.CLARIFICATION, reason="La résolution read-only est incomplète.")
        model = (intent.model or intent.name or "").strip()
        if not model:
            return RouteDecision(kind=RouteKind.CLARIFICATION, reason="Le modèle métier est obligatoire pour router l’intention.")
        try:
            records = self.ndx.search(model)
        except Exception as exc:
            return RouteDecision(kind=RouteKind.CUSTOM_PLAN, reason=f"NDX indisponible ; fallback custom contrôlé: {exc}")
        exact = [record for record in records if isinstance(record, dict) and str(record.get("model") or record.get("display_name") or "").strip() == model]
        if len(exact) == 1:
            return RouteDecision(kind=RouteKind.NDX_IMPORT, reason="Correspondance NDX exacte.", ndx_record=exact[0])
        if len(exact) > 1:
            return RouteDecision(kind=RouteKind.CLARIFICATION, reason="Plusieurs records NDX correspondent exactement au modèle.")
        return RouteDecision(kind=RouteKind.CUSTOM_PLAN, reason="Aucune correspondance NDX exacte ; plan custom.")
