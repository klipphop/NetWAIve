from netwaive.contracts import ChangePlan
from netwaive.gatekeeper import GatekeeperAgent


def test_change_plan_executes_one_batch():
    class FakeMCP:
        def __init__(self): self.calls = []
        def call(self, name, args): self.calls.append((name, args)); return {"ok": True}
        def tools(self): return []
    plan = ChangePlan(summary="lab", operations=[{"method": "POST", "endpoint": "/dcim/sites/", "data": {"name": "lab"}}])
    mcp = FakeMCP()
    result = GatekeeperAgent(object(), mcp, "model").confirm(plan)
    assert result.tool_results[0].ok is True
    assert mcp.calls == [("netbox_batch_execute", plan.mcp_arguments())]
