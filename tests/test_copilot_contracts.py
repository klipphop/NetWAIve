from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from netwaive.contracts import BatchOperation, ChangePlan


def test_endpoint_normalization_accepts_both_contracts():
    assert BatchOperation(method="POST", endpoint="/dcim/sites/", data={}).endpoint == "/dcim/sites/"
    assert BatchOperation(method="POST", endpoint="/api/dcim/sites/", data={}).endpoint == "/dcim/sites/"


def test_contract_rejects_legacy_keys_and_methods():
    try:
        BatchOperation(method="create", endpoint="/dcim/sites/", data={})
    except ValueError:
        pass
    else:
        raise AssertionError("legacy method accepted")
    try:
        BatchOperation.model_validate({"method": "POST", "endpoint": "/dcim/sites/", "data": {}, "action": "create"})
    except ValueError:
        pass
    else:
        raise AssertionError("legacy key accepted")


def test_change_plan_produces_mcp_payload():
    plan = ChangePlan(summary="Create site", operations=[BatchOperation(method="POST", endpoint="dcim/sites", data={"name": "lab"})])
    assert plan.count == 1
    assert plan.mcp_arguments() == {"operations": [{"method": "POST", "endpoint": "/dcim/sites/", "data": {"name": "lab"}}]}
