from netwaive.gatekeeper import GatekeeperAgent


def test_batch_contract_normalizes_calls_alias_and_rejects_legacy_keys():
    normalized = GatekeeperAgent._normalize_batch_arguments({"calls": [{"method": "POST", "endpoint": "/api/dcim/sites/", "data": {"name": "lab"}}]})
    assert normalized["operations"][0]["method"] == "POST"
    assert normalized["operations"][0]["endpoint"] == "/api/dcim/sites/"
    try:
        GatekeeperAgent._normalize_batch_arguments({"operations": [{"action": "create", "endpoint": "/dcim/sites/", "data": {}}]})
    except ValueError as exc:
        assert "method" in str(exc)
    else:
        raise AssertionError("legacy action key must be rejected")
