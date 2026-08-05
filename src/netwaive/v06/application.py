from __future__ import annotations

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
        resolver = ReadOnlyResolver(PynetboxReadOnlyGateway(self.tools))
        self.extractor = ReadOnlyIntentExtractor(OpenAI(base_url=settings.llm_base_url, api_key=settings.llm_api_key.get_secret_value(), timeout=settings.llm_timeout), self.tools, resolver, settings.llm_model, settings.max_agent_turns)
        self.pipeline = V06Pipeline(resolver, IntentRouter(self.tools.ndx), DeterministicPlanner(), ExecutionEngine(self.tools))

    def plan(self, request_text: str, scope: SessionScope):
        intent = self.extractor.extract(request_text)
        plan = self.pipeline.plan(scope, request_text, intent)
        return plan

    def confirm(self, scope: SessionScope, fingerprint: str):
        return self.pipeline.confirm(scope, fingerprint)
