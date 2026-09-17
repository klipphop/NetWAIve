from netwaive.contracts import ChangePlan
from netwaive.orchestrator import CopilotOrchestrator


def test_orchestrator_select_and_attach_plan():
    orchestrator = CopilotOrchestrator(None)
    selection = orchestrator.select({"results": [{"id": 1, "display": "A"}, {"id": 2, "display": "B"}]}, endpoint="dcim/things", exclude_keys={"dcim/things/2": "scope excluded"})
    plan = ChangePlan(summary="x", operations=[{"method": "POST", "endpoint": "/dcim/things/", "data": {"name": "A"}}])
    bound = orchestrator.attach_selection(plan, selection)
    assert bound.selection.selected[0].object_id == 1
    assert bound.selection.excluded[0].key == "dcim/things/2"
