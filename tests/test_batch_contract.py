from netwaive.gatekeeper import GatekeeperAgent


def test_batch_contract_normalizes_calls_alias_and_rejects_legacy_keys():
    normalized = GatekeeperAgent._batch({"calls": [{"method": "POST", "endpoint": "/api/dcim/sites/", "data": {"name": "lab"}}]})
    assert normalized.operations[0].method == "POST"
    assert normalized.operations[0].endpoint == "/dcim/sites/"
    try:
        GatekeeperAgent._batch({"operations": [{"action": "create", "endpoint": "/dcim/sites/", "data": {}}]})
    except Exception as exc:
        assert "method" in str(exc) or "extra" in str(exc)
    else:
        raise AssertionError("legacy action key must be rejected")
