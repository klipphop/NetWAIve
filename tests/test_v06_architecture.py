from __future__ import annotations

import pytest

from netwaive.models import PendingToolCall, ToolResult
from netwaive.v06.contracts import IntentResolution, ResolutionKind, ResolvedRef, RouteDecision, RouteKind, CertifiedPlanInput
from netwaive.v06.execution import ExecutionEngine
from netwaive.v06.planner import DeterministicPlanner, intent_fingerprint
from netwaive.v06.resolver import ReadOnlyResolver
from netwaive.v06.router import IntentRouter
from netwaive.v06.session import SessionScope


class Gateway:
    def __init__(self, records): self.records, self.calls = records, []
    def read(self, app, endpoint, *, filters):
        self.calls.append((app, endpoint, filters))
        return self.records.get((endpoint, tuple(filters.items())), [])


def test_read_only_resolver_returns_exact_certified_id_and_never_writes():
    gateway = Gateway({("sites", (("name", "FR01"),)): [{"id": 15, "name": "FR01", "display": "FR01 - Le Fief-Sauvin"}]})
    result = ReadOnlyResolver(gateway).resolve(ResolutionKind.SITE, "FR01")
    assert result.id == 15
    assert result.display == "FR01 - Le Fief-Sauvin"
    assert gateway.calls == [("dcim", "sites", {"name": "FR01"})]


def test_resolver_rejects_ambiguous_and_missing_exact_records():
    gateway = Gateway({("manufacturers", (("name", "Generic"),)): [{"id": 14, "name": "Generic"}, {"id": 15, "name": "Generic"}]})
    resolver = ReadOnlyResolver(gateway)
    assert resolver.resolve(ResolutionKind.MANUFACTURER, "Generic").code == "ambiguous"
    assert resolver.resolve(ResolutionKind.SITE, "NOPE").code == "not_found"


def test_router_selects_ndx_only_on_exact_model():
    class NDX:
        def search(self, model): return [{"model": model}] if model == "Power Strip 8 ports" else [{"model": "Other"}]
    intent = IntentResolution(request_text="x", model="Power Strip 8 ports")
    assert IntentRouter(NDX()).route(intent).kind == RouteKind.NDX_IMPORT
    custom = IntentResolution(request_text="x", model="Custom PDU")
    assert IntentRouter(NDX()).route(custom).kind == RouteKind.CUSTOM_PLAN


def certified(intent):
    route = RouteDecision(kind=RouteKind.CUSTOM_PLAN, reason="custom")
    return CertifiedPlanInput(session_id="s", generation="g", fingerprint=intent_fingerprint(intent.request_text, intent), intent=intent, route=route)


def test_planner_is_pure_and_places_parent_before_exactly_eight_children():
    intent = IntentResolution(
        request_text="8 ports", model="Power Strip 8 ports",
        refs={"manufacturer": ResolvedRef(id=14, app="dcim", endpoint="manufacturers", kind=ResolutionKind.MANUFACTURER, display="Generic", requested="Generic")},
        explicit_count=8, component_templates={"power-ports": [{"name": "Power Port", "type": "type-e", "quantity": 8}]},
    )
    plan = DeterministicPlanner().build(certified(intent))
    assert plan.calls[0].arguments["data"] == {"model": "Power Strip 8 ports", "manufacturer": 14}
    assert len(plan.calls) == 9
    assert [call.arguments["data"]["name"] for call in plan.calls[1:]] == [f"Power Port {i}" for i in range(1, 9)]
    assert all(call.arguments["data"]["device_type"] == "${device-type.data.id}" for call in plan.calls[1:])


def test_execution_engine_is_blind_and_rejects_stale_scope():
    class Port:
        def __init__(self): self.calls = []
        def execute(self, name, arguments): self.calls.append((name, arguments)); return ToolResult(ok=True, message="ok", data={"id": 1})
        def import_ndx_object(self, arguments): self.calls.append(("ndx", arguments)); return ToolResult(ok=True, message="ok", data={"id": 1})
    port = Port(); engine = ExecutionEngine(port)
    plan = DeterministicPlanner().build(certified(IntentResolution(
        request_text="x", model="X", refs={"manufacturer": ResolvedRef(id=14, app="dcim", endpoint="manufacturers", kind=ResolutionKind.MANUFACTURER, display="Generic", requested="Generic")}
    )))
    assert engine.execute(plan, session_id="other", generation="g", fingerprint=plan.fingerprint).ok is False
    assert port.calls == []
    assert engine.execute(plan, session_id="s", generation="g", fingerprint=plan.fingerprint).ok is True


def test_pipeline_facade_enforces_plan_then_confirm_scope():
    from netwaive.v06.pipeline import V06Pipeline
    from netwaive.v06.resolver import ReadOnlyResolver
    from netwaive.v06.session import SessionScope
    class NDX:
        def search(self, model): return []
    class Port:
        def execute(self, name, arguments): return ToolResult(ok=True, message="ok", data={"id": 1})
        def import_ndx_object(self, arguments): return ToolResult(ok=True, message="ok", data={"id": 1})
    gateway = Gateway({})
    intent = IntentResolution(request_text="x", model="X", refs={"manufacturer": ResolvedRef(id=1, app="dcim", endpoint="manufacturers", kind=ResolutionKind.MANUFACTURER, display="Generic", requested="Generic")})
    scope = SessionScope.new("session")
    pipeline = V06Pipeline(ReadOnlyResolver(gateway), IntentRouter(NDX()), DeterministicPlanner(), ExecutionEngine(Port()))
    plan = pipeline.plan(scope, "x", intent)
    assert pipeline.confirm(scope, plan.fingerprint).ok
    with pytest.raises(ValueError): pipeline.confirm(scope, plan.fingerprint)
    scope = SessionScope.new("s")
    generation = scope.begin_request()
    intent = IntentResolution(request_text="x", model="X", refs={"manufacturer": ResolvedRef(id=1, app="dcim", endpoint="manufacturers", kind=ResolutionKind.MANUFACTURER, display="Generic", requested="Generic")})
    plan = DeterministicPlanner().build(CertifiedPlanInput(session_id="s", generation=generation, fingerprint=intent_fingerprint(intent.request_text, intent), intent=intent, route=RouteDecision(kind=RouteKind.CUSTOM_PLAN, reason="custom")))
    scope.store(plan)
    scope.begin_request()
    with pytest.raises(ValueError): scope.consume(plan.fingerprint)


def test_readonly_resolver_rejects_non_exact_backend_record():
    gateway = Gateway({("dcim", "sites"): [{"id": 7, "name": "FR01X", "display": "FR01X"}]})
    result = ReadOnlyResolver(gateway).resolve(ResolutionKind.SITE, "FR01")
    assert getattr(result, "code", None) == "not_found"


def test_planner_rejects_forged_fingerprint():
    intent = IntentResolution(request_text="x", model="X", refs={"manufacturer": ResolvedRef(id=1, app="dcim", endpoint="manufacturers", kind=ResolutionKind.MANUFACTURER, display="Generic", requested="Generic")})
    certified_input = CertifiedPlanInput(session_id="s", generation="g", fingerprint="forged", intent=intent, route=RouteDecision(kind=RouteKind.CUSTOM_PLAN, reason="custom"))
    with pytest.raises(ValueError, match="Fingerprint"):
        DeterministicPlanner().build(certified_input)
