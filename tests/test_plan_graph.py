from netwaive.gatekeeper import GatekeeperAgent


def test_forward_reference_rejected():
    try:
        GatekeeperAgent._batch({"operations": [
            {"method": "PATCH", "endpoint": "/dcim/devices/${1.id}/", "data": {"name": "x"}},
            {"method": "POST", "endpoint": "/dcim/sites/", "data": {"name": "site"}},
        ]})
    except ValueError as exc:
        assert "forward" in str(exc)
    else:
        raise AssertionError("forward reference must be rejected before pending")


def test_unsupported_reference_rejected():
    try:
        GatekeeperAgent._batch({"operations": [{"method": "POST", "endpoint": "/dcim/sites/", "data": {"name": "${foo.bar}"}}]})
    except ValueError as exc:
        assert "unsupported" in str(exc)
    else:
        raise AssertionError("unsupported reference must be rejected")
