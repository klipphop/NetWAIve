from __future__ import annotations

import re
from openai import OpenAI

from ..config import Settings
from ..tools import NetBoxTools
from .execution import ExecutionEngine
from .intent import ReadOnlyIntentExtractor
from .pipeline import V06Pipeline
from .read_gateway import PynetboxReadOnlyGateway
from .resolver import ReadOnlyResolver
from .router import IntentRouter
from .planner import DeterministicPlanner
from .session import SessionScope


class V06Application:
    """Production adapter: read-only extraction, then strict v0.6 pipeline."""

    def __init__(self, settings: Settings):
        self.tools = NetBoxTools(settings)
        self.read_gateway = PynetboxReadOnlyGateway(self.tools)
        resolver = ReadOnlyResolver(self.read_gateway)
        self.extractor = ReadOnlyIntentExtractor(
            OpenAI(
                base_url=settings.llm_base_url,
                api_key=settings.llm_api_key.get_secret_value(),
                timeout=settings.llm_timeout,
            ),
            self.tools,
            resolver,
            settings.llm_model,
            settings.max_agent_turns,
        )
        self.pipeline = V06Pipeline(
            resolver,
            IntentRouter(self.tools.ndx),
            DeterministicPlanner(),
            ExecutionEngine(self.tools),
        )

    def read_only_response(self, request_text: str) -> str | None:
        """Serve unambiguous RO requests without entering the planner."""
        text = request_text.casefold().strip()
        if re.fullmatch(r"(?:salut|bonjour|hello|hi|hey)[!. ]*", text):
            return "Bonjour. Je peux consulter NetBox en lecture seule ou préparer un plan de modification à confirmer."
        if re.search(r"\b(?:liste|list|lister|affiche|afficher|show)\b.*\b(?:device|devices|équipements|equipements)\b", text):
            site_match = re.search(r"\b(?:site|du site|at)\s+(.+?)\s*[?.!]*$", request_text, re.IGNORECASE)
            if not site_match:
                site_match = re.search(r"\b(?:devices?|équipements?)\s+(.+?)\s*[?.!]*$", request_text, re.IGNORECASE)
            if not site_match:
                return "Précisez le site à consulter."
            site_name = site_match.group(1).strip(" \t?.!")
            sites = self.read_gateway.read("dcim", "sites", filters={"name": site_name})
            if not sites and " - " in site_name:
                sites = self.read_gateway.read("dcim", "sites", filters={"name": site_name.split(" - ", 1)[0]})
            exact_sites = [
                item for item in sites
                if str(item.get("name") or "") == site_name or str(item.get("display") or "") == site_name
            ]
            if len(exact_sites) != 1:
                return "Site introuvable ou ambigu : " + site_name
            site_id = exact_sites[0].get("id")
            if isinstance(site_id, bool) or not isinstance(site_id, int) or site_id <= 0:
                return "Le site résolu ne possède pas d’identifiant valide."
            devices = self.read_gateway.read("dcim", "devices", filters={"site_id": site_id})
            if not devices:
                return f"Aucun device dans {site_name}."
            names = [str(item.get("display") or item.get("name") or item.get("id")) for item in devices]
            return f"Devices de {site_name} :\n" + "\n".join(f"- {name}" for name in names)
        if re.search(r"\b(?:liste|list|lister|affiche|afficher|show)\b.*\b(?:site|sites)\b", text):
            records = self.read_gateway.read("dcim", "sites", filters={})
            if not records:
                return "Aucun site NetBox trouvé."
            names = [str(item.get("display") or item.get("name") or item.get("id")) for item in records]
            return "Sites NetBox :\n" + "\n".join(f"- {name}" for name in names)
        return None

    def plan(self, request_text: str, scope: SessionScope):
        intent = self.extractor.extract(request_text)
        plan = self.pipeline.plan(scope, request_text, intent)
        return plan

    def confirm(self, scope: SessionScope, fingerprint: str):
        return self.pipeline.confirm(scope, fingerprint)
