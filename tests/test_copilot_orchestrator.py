from netwaive.contracts import BatchOperation, ChangePlan
from netwaive.orchestrator import CopilotOrchestrator


class FakeMCP:
    def __init__(self):
        self.calls = []

    def call(self, name, arguments):
        self.calls.append((name, arguments))
        return {"ok": True}


def test_validate_rejects_patch_without_id():
    plan = ChangePlan(summary="bad", operations=[BatchOperation(method="PATCH", endpoint="/dcim/sites/", data={})])
    try:
        CopilotOrchestrator(FakeMCP()).validate(plan)
    except ValueError:
        pass
    else:
        raise AssertionError("PATCH without numeric id accepted")


def test_execute_validates_then_calls_batch_once():
    fake = FakeMCP()
    plan = ChangePlan(summary="create", operations=[BatchOperation(method="POST", endpoint="/dcim/sites/", data={"name": "lab"})])
    assert CopilotOrchestrator(fake).execute(plan) == {"ok": True}
    assert fake.calls == [("netbox_batch_execute", {"operations": [{"method": "POST", "endpoint": "/dcim/sites/", "data": {"name": "lab"}}]})]
