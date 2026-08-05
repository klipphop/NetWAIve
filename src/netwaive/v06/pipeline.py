from __future__ import annotations

from typing import Any

from .contracts import CertifiedPlanInput, ExecutionReport, IntentResolution, PendingPlan, RouteDecision
from .execution import ExecutionEngine
from .planner import DeterministicPlanner, intent_fingerprint
from .resolver import ReadOnlyResolver
from .router import IntentRouter
from .session import SessionScope


class V06Pipeline:
    """Façade publique v0.6: aucune étape ne peut sauter une couche."""

    def __init__(self, resolver: ReadOnlyResolver, router: IntentRouter, planner: DeterministicPlanner, engine: ExecutionEngine):
        self.resolver = resolver
        self.router = router
        self.planner = planner
        self.engine = engine

    def plan(self, scope: SessionScope, request_text: str, intent: IntentResolution) -> PendingPlan:
        generation = scope.begin_request()
        if not self.resolver.verify(intent):
            raise ValueError("Intent non certifiée par cette instance du Resolver.")
        route = self.router.route(intent)
        certified = CertifiedPlanInput(
            session_id=scope.session_id,
            generation=generation,
            fingerprint=intent_fingerprint(request_text, intent),
            intent=intent,
            route=route,
        )
        plan = self.planner.build(certified)
        scope.store(plan)
        return plan

    def confirm(self, scope: SessionScope, fingerprint: str) -> ExecutionReport:
        plan = scope.consume(fingerprint)
        return self.engine.execute(plan, session_id=scope.session_id, generation=scope.generation, fingerprint=fingerprint)
