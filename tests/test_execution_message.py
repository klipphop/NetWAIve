from netwaive.gatekeeper import GatekeeperAgent
from netwaive.schemas import ToolResult
from netwaive.contracts import ChangePlan, BatchOperation


def test_delete_dependency_error_is_business_message():
    plan = ChangePlan(summary="x", operations=[BatchOperation(method="DELETE", endpoint="/dcim/sites/24/", data={})])
    result = ToolResult(ok=False, message='netbox_batch_execute failed: NetBox API HTTP 409: {"detail": "Unable to delete object. 2 dependent objects were found: VLAN-A (1) (2), PREFIX-B (3) (4)"}')
    message = GatekeeperAgent._execution_message(result, plan)
    assert "2 dépendance(s)" in message
    assert "Aucune suppression" in message
    assert "VLAN-A" in message
