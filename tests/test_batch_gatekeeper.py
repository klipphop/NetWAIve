from netwaive.gatekeeper import GatekeeperAgent
from netwaive.models import PendingToolCall


def test_batch_is_one_write_and_confirmed_once():
    class FakeMCP:
        def __init__(self): self.calls = []
        def call(self, name, args): self.calls.append((name, args)); return {"ok": True}
        def tools(self): return []
    class FakeLLM:
        pass
    mcp = FakeMCP()
    call = PendingToolCall(id="batch-1", name="netbox_batch_execute", arguments={"operations": [{"method": "POST", "endpoint": "/dcim/sites/", "data": {"name": "lab"}}]})
    assert GatekeeperAgent._is_write("netbox_batch_execute") is True
    assert "1 opération" in GatekeeperAgent._pending_message([call])
    result = GatekeeperAgent(FakeLLM(), mcp, "model").confirm([call])
    assert result.tool_results[0].ok is True
    assert mcp.calls == [("netbox_batch_execute", call.arguments)]
