import pytest

from netwaive.agent import NetBoxAgent
from netwaive.composite import NDXCompositeImporter
from netwaive.errors import NetBoxChatError
from netwaive.models import ToolResult
from netwaive.tools import NetBoxTools


class RoleEndpoint:
    def filter(self, **kwargs):
        return [
            {"id": 31, "name": "Server", "slug": "server"},
            {"id": 12, "name": "Switch", "slug": "switch"},
        ]


def test_device_create_autofills_live_switch_role_and_required_slug():
    tools = object.__new__(NetBoxTools)
    tools.resolver_observed_ids = set()
    tools._resolve_endpoint = lambda app, endpoint: (RoleEndpoint(), "dcim", None, endpoint)
    tools.get_endpoint_schema = lambda args: ToolResult(
        ok=True,
        message="schema",
        data={"required_fields": ["role", "slug"], "writable_fields": {"role": {}, "slug": {}}},
    )

    enriched = tools.enrich_write_arguments({
        "app": "dcim",
        "endpoint": "devices",
        "action": "create",
        "data": {"name": "Core Switch 01"},
    })

    assert enriched["data"]["role"] == 12
    assert enriched["data"]["slug"] == "core-switch-01"
    assert tools.resolver_observed_ids == {12}


def test_device_role_id_requires_live_observation_or_resolver():
    arguments = {"app": "dcim", "endpoint": "devices", "action": "create", "data": {"name": "SW-X", "role": 987654}}
    rejected = NetBoxAgent._write_guard(arguments, {("dcim", "devices")}, set(), "en", {("dcim", "devices"): []})
    assert rejected is not None
    assert rejected.data["unobserved_role_id"] is True
    assert NetBoxAgent._write_guard(arguments, {("dcim", "devices")}, {987654}, "en", {("dcim", "devices"): []}) is None


def test_device_role_falls_back_to_lowest_live_id_without_switch():
    class GenericRoles:
        def filter(self, **kwargs):
            return [{"id": 31, "name": "Server"}, {"id": 12, "name": "Network"}]

    tools = object.__new__(NetBoxTools)
    tools.resolver_observed_ids = set()
    tools._resolve_endpoint = lambda app, endpoint: (GenericRoles(), "dcim", None, endpoint)
    assert tools._resolve_device_role() == 12
    assert tools.resolver_observed_ids == {12}


def test_device_role_accepts_switch_name_ignores_nonpositive_ids_and_hides_api_errors():
    class MixedRoles:
        def filter(self, **kwargs):
            return [
                {"id": -4, "name": "Invalid"},
                {"id": 8, "name": "Switch", "slug": "custom-role"},
                {"id": 3, "name": "Server", "slug": "server"},
            ]

    tools = object.__new__(NetBoxTools)
    tools.resolver_observed_ids = set()
    tools._resolve_endpoint = lambda app, endpoint: (MixedRoles(), "dcim", None, endpoint)
    assert tools._resolve_device_role() == 8

    tools.get_endpoint_schema = lambda args: ToolResult(ok=True, message="schema", data={"required_fields": ["role"]})
    tools._resolve_endpoint = lambda app, endpoint: (_ for _ in ()).throw(RuntimeError("secret backend detail"))
    enriched = tools.enrich_write_arguments({"app": "dcim", "endpoint": "devices", "action": "create", "data": {"name": "SW-01"}})
    assert "role" not in enriched["data"]


def test_ndx_existing_lookup_is_scoped_by_manufacturer():
    class DeviceTypes:
        def filter(self, **kwargs):
            return [
                {"id": 1, "model": "Shared Model", "manufacturer": {"name": "Other Vendor", "slug": "other-vendor"}},
                {"id": 2, "model": "Shared Model", "manufacturer": {"name": "Target Vendor", "slug": "target-vendor"}},
            ]

    tools = object.__new__(NetBoxTools)
    tools._resolve_endpoint = lambda app, endpoint: (DeviceTypes(), "dcim", None, endpoint)
    match = tools._existing_ndx_parent("Shared Model", "device-type", "Target Vendor")
    assert match is not None
    assert match.data["manufacturer"]["name"] == "Target Vendor"
    assert tools._existing_ndx_parent("Shared Model", "device-type", "Missing Vendor") is None


def test_ndx_lookup_reads_pynetbox_relation_before_serialize():
    class Manufacturer:
        name = "Target Vendor"
        slug = "target-vendor"
        display = "Target Vendor"
        def __str__(self): return self.name

    class DeviceTypeRecord:
        manufacturer = Manufacturer()
        def serialize(self):
            return {"id": 2, "model": "Shared Model", "manufacturer": 42}

    class DeviceTypes:
        def filter(self, **kwargs): return [DeviceTypeRecord()]

    tools = object.__new__(NetBoxTools)
    tools._resolve_endpoint = lambda app, endpoint: (DeviceTypes(), "dcim", None, endpoint)
    match = tools._existing_ndx_parent("Shared Model", "device-type", "Target Vendor")
    assert match is not None
    assert match.data["model"] == "Shared Model"


def test_ndx_import_bypasses_every_write_when_parent_exists():
    calls = []

    def find_existing(arguments):
        return None

    def find_existing_parent(model, object_type, manufacturer):
        calls.append(("read", "device-types"))
        assert (model, object_type, manufacturer) == ("Existing 48P", "device-type", "Generic")
        return ToolResult(ok=True, message="existing", data={"id": 77, "model": model, "manufacturer": {"name": manufacturer}})

    def execute(name, arguments):
        calls.append(("write", arguments["endpoint"]))
        raise AssertionError("No NetBox write may occur for an existing NDX parent")

    payload = {
        "object_type": "device-type",
        "manufacturer": "Generic",
        "parent": {"model": "Existing 48P", "slug": "existing-48p"},
        "component_templates": {"interfaces": [{"name": "eth0", "type": "1000base-t"}]},
    }
    result = NDXCompositeImporter(find_existing, execute, find_existing_parent).run(payload)

    assert result.ok
    assert result.data["skipped"] is True
    assert calls == [("read", "device-types")]


def test_ndx_import_does_not_bypass_wrong_parent_record():
    sequence = []

    def existing_parent(model, object_type, manufacturer):
        return ToolResult(ok=True, message="wrong", data={"id": 99, "model": "OTHER", "manufacturer": {"name": manufacturer}})

    def execute(name, arguments):
        sequence.append(arguments["endpoint"])
        return ToolResult(ok=True, message="created", data={"id": len(sequence)})

    payload = {
        "object_type": "device-type",
        "manufacturer": "Generic",
        "parent": {"model": "Expected", "slug": "expected"},
        "component_templates": {"interfaces": [{"name": "eth0", "type": "1000base-t"}]},
    }
    result = NDXCompositeImporter(lambda arguments: None, execute, existing_parent).run(payload)
    assert result.ok
    assert sequence == ["manufacturers", "device-types", "interface-templates"]


def test_ndx_lookup_and_import_hide_backend_exception_details():
    tools = object.__new__(NetBoxTools)
    tools._resolve_endpoint = lambda app, endpoint: (_ for _ in ()).throw(RuntimeError("SECRET_BACKEND_DETAIL"))
    with pytest.raises(NetBoxChatError) as caught:
        tools._existing_ndx_parent("Model", "device-type", "Vendor")
    assert "SECRET_BACKEND_DETAIL" not in str(caught.value)

    tools.find_existing_create = lambda arguments: None
    tools.execute = lambda name, arguments: ToolResult(ok=True, message="ok", data={"id": 1})
    tools._existing_ndx_parent = lambda model, object_type, manufacturer: (_ for _ in ()).throw(NetBoxChatError("Vérification NetBox impossible ; import NDX bloqué."))
    result = tools.import_ndx_object({"payload": {
        "object_type": "device-type",
        "manufacturer": "Vendor",
        "parent": {"model": "Model", "slug": "model"},
        "component_templates": {"interfaces": [{"name": "eth0", "type": "1000base-t"}]},
    }})
    assert not result.ok
    assert result.data["reason"] == "netbox_lookup_failed"
    assert "SECRET_BACKEND_DETAIL" not in result.message


def test_generic_create_dedup_lookup_fails_closed():
    class BrokenEndpoint:
        def filter(self, **kwargs):
            raise RuntimeError("SECRET_BACKEND_DETAIL")

    tools = object.__new__(NetBoxTools)
    tools._resolve_endpoint = lambda app, endpoint: (BrokenEndpoint(), app, None, endpoint)
    with pytest.raises(NetBoxChatError, match="création bloquée") as caught:
        tools.find_existing_create({
            "app": "dcim", "endpoint": "sites", "action": "create", "data": {"name": "LAB"}
        })
    assert "SECRET_BACKEND_DETAIL" not in str(caught.value)
