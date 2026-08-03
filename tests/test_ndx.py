from pathlib import Path

import pytest

from netwaive.composite import NDXCompositeImporter
from netwaive.models import NDXImportPayload, ToolResult
from netwaive.ndx import NDXConnector
from netwaive.tools import NetBoxTools


VENDORS = [
    {"display_name": "Juniper Networks", "slug": "juniper"},
    {"display_name": "Fortinet", "slug": "fortinet"},
    {"display_name": "Arista Networks", "slug": "arista"},
]

RECORDS = [
    {"vendor_name":"Juniper Networks", "manufacturer": "Juniper", "model": "EX4400-24X", "part_number": "EX4400-24X", "slug": "ex4400-24x", "type":"device-type", "source":"community"},
    {"vendor_name":"Fortinet", "manufacturer": "Fortinet", "model": "FortiGate 100F", "part_number": "FG-100F", "slug": "fg-100f", "type":"device-type", "source":"community"},
    {"vendor_name":"Arista Networks", "manufacturer": "Arista", "model": "DCS-7050SX3-48YC8-F", "part_number": "DCS-7050SX3-48YC8-F", "slug": "dcs-7050sx3-48yc8-f", "type":"device-type", "source":"community"},
]


class Response:
    def __init__(self, payload=None, status_code=200, text=""):
        self.payload = payload
        self.status_code = status_code
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)

    def json(self):
        return self.payload


class CatalogSession:
    def get(self, url, timeout):
        if url.endswith("vendors.json"):
            return Response(VENDORS)
        if url.endswith("search-index.json"):
            return Response(RECORDS)
        if url.endswith("/repos/netbox-community/devicetype-library"):
            return Response({"default_branch":"catalog-default"})
        if "/contents/device-types/" in url:
            return Response([{"name":f"{item['part_number']}.yaml","download_url":f"https://spec.invalid/{item['part_number']}.yaml"} for item in RECORDS])
        if url.startswith("https://spec.invalid/"):
            return Response(text="interfaces:\n  - name: port1\n")
        raise AssertionError(f"unexpected URL: {url}")


@pytest.mark.parametrize(
    ("query", "expected"),
    [("JN", "Juniper Networks"), ("Fortinet", "Fortinet"), ("Arista", "Arista Networks")],
)
def test_vendor_resolution_uses_catalog_metadata(query, expected):
    connector = NDXConnector(CatalogSession())
    assert connector.resolve_vendor(query)["display_name"] == expected


@pytest.mark.parametrize("part_number", ["EX4400-24X", "FG-100F", "DCS-7050SX3-48YC8-F"])
def test_exact_multivendor_specs_build_composite_payload(part_number):
    connector = NDXConnector(CatalogSession())
    payload = connector.build_payload(part_number)
    assert payload is not None
    assert payload["parent"]["slug"]
    assert len(payload["component_templates"]["interfaces"]) == 1


class DetailedSpecSession(CatalogSession):
    def get(self, url, timeout):
        if url.endswith("/repos/netbox-community/devicetype-library"):
            return Response({"default_branch": "catalog-default"})
        if "/contents/device-types/" in url:
            assert "ref=catalog-default" in url
            return Response([{"name":"EX4400-24X.yaml","download_url":"https://spec.invalid/EX4400-24X.yaml"}])
        if url == "https://spec.invalid/EX4400-24X.yaml":
            return Response(status_code=200, text="interfaces:\n  - name: ge-0/0/0\n  - name: ge-0/0/1\nconsole-ports:\n  - name: Console\n")
        return super().get(url, timeout)


def test_detailed_spec_uses_discovered_branch_and_preserves_counts():
    records = [dict(RECORDS[0], component_templates={})]

    class Session(DetailedSpecSession):
        def get(self, url, timeout):
            if url.endswith("search-index.json"):
                return Response(records)
            return super().get(url, timeout)

    payload = NDXConnector(Session()).build_payload("EX4400-24X")
    assert len(payload["component_templates"]["interfaces"]) == 2
    assert len(payload["component_templates"]["console-ports"]) == 1


def test_detailed_spec_resolves_filename_from_directory_metadata():
    record = {"vendor_name":"Allied Telesis","manufacturer":"Allied Telesis","model":"GS950/8","part_number":"AT-GS950/8","slug":"allied-telesis-gs950-8","type":"device-type","source":"both"}
    class Session(CatalogSession):
        def get(self, url, timeout):
            if url.endswith("vendors.json"): return Response([{"display_name":"Allied Telesis","slug":"allied-telesis"}])
            if url.endswith("search-index.json"): return Response([record])
            if url.endswith("/repos/netbox-community/devicetype-library"): return Response({"default_branch":"master"})
            if "/contents/device-types/" in url: return Response([{"name":"GS950-8.yaml","download_url":"https://spec.invalid/GS950-8.yaml"}])
            if url == "https://spec.invalid/GS950-8.yaml": return Response(text="interfaces:\n  - name: Port1\n")
            raise AssertionError(url)
    payload = NDXConnector(Session()).build_payload("AT-GS950/8")
    assert len(payload["component_templates"]["interfaces"]) == 1


def test_search_indexes_nested_technical_metadata_without_field_rules():
    record = {"manufacturer":"Generic","model":"Access 48","part_number":"ACC-48","type":"device-type","technical":{"interfaces":{"speeds":["1G","2.5G"],"poe":True}}}
    class Session(CatalogSession):
        def get(self, url, timeout):
            if url.endswith("vendors.json"): return Response([{"display_name":"Generic","slug":"generic"}])
            if url.endswith("search-index.json"): return Response([record])
            raise AssertionError(url)
    matches = NDXConnector(Session()).search("2.5G")
    assert matches == [record]


def test_netbox_labs_only_record_without_public_components_is_rejected_before_pending():
    record = {"vendor_name":"Generic Networks","manufacturer":"Generic","model":"Core 1000","part_number":"CORE-1000","slug":"core-1000","type":"device-type","source":"netbox_labs"}
    vendors = [{"display_name":"Generic Networks","slug":"generic"}]
    class Session(CatalogSession):
        def get(self, url, timeout):
            if url.endswith("vendors.json"): return Response(vendors)
            if url.endswith("search-index.json"): return Response([record])
            raise AssertionError(f"detail fetch forbidden for NetBox Labs-only record: {url}")
    connector = NDXConnector(Session())
    payload = connector.build_payload("CORE-1000")
    assert payload["component_templates"] == {}


def test_exact_match_rejects_non_device_types():
    connector = NDXConnector(CatalogSession())
    candidates = [{"type":"module-type","model":"MOD-1","part_number":"MOD-1"}]
    assert connector.exact("MOD-1", candidates) is None
    assert connector.exact("MOD-1", candidates, object_type="module-type") == candidates[0]


@pytest.mark.parametrize("payload", [
    {"object_type":"device-type","manufacturer":" ","parent":{"model":"M","slug":"m"},"component_templates":{"interfaces":[{"name":"p1"}]}},
    {"object_type":"device-type","manufacturer":"Generic","parent":{"model":" ","slug":"m"},"component_templates":{"interfaces":[{"name":"p1"}]}},
    {"object_type":"device-type","manufacturer":"Generic","parent":{"model":"M","slug":"m"},"component_templates":{"interfaces":[{}]}},
    {"object_type":"device-type","manufacturer":"Generic","parent":{"model":"M","slug":"m"},"component_templates":{"unknown":[{"name":"x"}]}},
])
def test_ndx_dto_rejects_malformed_payloads(payload):
    with pytest.raises(ValueError):
        NDXImportPayload.model_validate(payload)


def test_prepare_refuses_empty_component_spec_before_pending():
    class EmptyConnector:
        def search(self, query):
            return [{"model": query}]
        def build_payload(self, query, object_type="device-type"):
            return {"object_type":object_type,"manufacturer":"Generic Networks","parent":{"model":query,"slug":"generic-model","u_height":1},"component_templates":{}}
        def exact(self, query, candidates, object_type="device-type"):
            return candidates[0]
    tools = NetBoxTools.__new__(NetBoxTools)
    tools.ndx = EmptyConnector()
    result = tools.prepare_ndx_object({"model":"Generic Model"}, "device-type")
    assert not result.ok
    assert result.data["reason"] == "empty_component_templates"


def test_module_type_import_is_complete_and_auto_includes_manufacturer():
    record = {"vendor_name":"Generic Networks","manufacturer":"Generic","model":"PSU-1000","part_number":"PSU-1000","slug":"psu-1000","type":"module-type","source":"community"}
    class ModuleSession(CatalogSession):
        def get(self, url, timeout):
            if url.endswith("vendors.json"): return Response([{"display_name":"Generic Networks","slug":"generic"}])
            if url.endswith("search-index.json"): return Response([record])
            if url.endswith("/repos/netbox-community/devicetype-library"): return Response({"default_branch":"master"})
            if "/contents/module-types/" in url: return Response([{"name":"PSU-1000.yaml","download_url":"https://spec.invalid/PSU-1000.yaml"}])
            if url == "https://spec.invalid/PSU-1000.yaml": return Response(text="manufacturer: Generic\nmodel: PSU-1000\npart_number: P-1000\nairflow: front-to-rear\npower-ports:\n  - name: Power Input\n    type: dc-terminal\n")
            raise AssertionError(url)

    payload = NDXConnector(ModuleSession()).build_payload("PSU-1000", object_type="module-type")
    assert payload["object_type"] == "module-type"
    assert payload["parent"]["part_number"] == "P-1000"
    assert payload["parent"]["slug"] == "psu-1000"
    assert payload["parent"]["airflow"] == "front-to-rear"
    assert len(payload["component_templates"]["power-ports"]) == 1

    writes = []
    def execute(name, arguments):
        writes.append(arguments)
        return ToolResult(ok=True, message="ok", data={"id": len(writes)})
    result = NDXCompositeImporter(lambda arguments: None, execute).run(payload)
    assert result.ok
    assert [item["endpoint"] for item in writes] == ["manufacturers", "module-types", "power-port-templates"]
    assert writes[2]["data"]["module_type"] == 2


def test_idempotence_scopes_children_to_parent_relation():
    class Record:
        def __init__(self, record_id, parent_id):
            self.record_id, self.parent_id = record_id, parent_id
        def serialize(self):
            return {"id":self.record_id,"name":"port1","device_type":{"id":self.parent_id}}
    class Endpoint:
        def filter(self, **kwargs): return [Record(10,1), Record(20,2)]
        def all(self, **kwargs): return []
    tools = NetBoxTools.__new__(NetBoxTools)
    tools._resolve_endpoint = lambda app, endpoint: (Endpoint(), app, None, endpoint)
    result = tools.find_existing_create({"app":"dcim","endpoint":"interface-templates","action":"create","data":{"name":"port1","device_type":2}})
    assert result.data["id"] == 20


def test_idempotence_pushes_parent_filter_before_result_limit():
    class Record:
        def __init__(self, record_id, parent_id): self.record_id, self.parent_id = record_id, parent_id
        def serialize(self): return {"id":self.record_id,"name":"port1","device_type":{"id":self.parent_id}}
    class Endpoint:
        def filter(self, **kwargs):
            records = [Record(i, i) for i in range(1, 22)]
            parent = kwargs.get("device_type_id")
            return [record for record in records if parent is None or record.parent_id == parent]
        def all(self, **kwargs): return []
    tools = NetBoxTools.__new__(NetBoxTools)
    tools._resolve_endpoint = lambda app, endpoint: (Endpoint(), app, None, endpoint)
    result = tools.find_existing_create({"app":"dcim","endpoint":"interface-templates","action":"create","data":{"name":"port1","device_type":21}})
    assert result.data["id"] == 21


def test_composite_rejects_blank_parent_identifier():
    def execute(name, arguments):
        return ToolResult(ok=True, message="ok", data={"id":""})
    payload = {"object_type":"device-type","manufacturer":"Generic","parent":{"model":"M","slug":"m"},"component_templates":{"interfaces":[{"name":"p1"}]}}
    result = NDXCompositeImporter(lambda arguments: None, execute).run(payload)
    assert not result.ok


def test_idempotence_never_uses_substring_name_matches():
    class Record:
        def __init__(self, record_id, name): self.record_id, self.name = record_id, name
        def serialize(self): return {"id":self.record_id,"name":self.name}
    class Endpoint:
        def filter(self, **kwargs): return []
        def all(self, **kwargs): return [Record(1,"port10")]
    tools = NetBoxTools.__new__(NetBoxTools)
    tools._resolve_endpoint = lambda app, endpoint: (Endpoint(), app, None, endpoint)
    assert tools.find_existing_create({"app":"dcim","endpoint":"interfaces","action":"create","data":{"name":"port1"}}) is None


def test_idempotence_pushes_all_parent_scopes_together():
    class Record:
        def serialize(self): return {"id":9,"name":"port1","module":{"id":7},"device_type":{"id":21}}
    class Endpoint:
        def filter(self, **kwargs):
            if kwargs.get("module_id") == 7 and kwargs.get("device_type_id") == 21: return [Record()]
            raise ValueError("incomplete scope")
        def all(self, **kwargs): return []
    tools = NetBoxTools.__new__(NetBoxTools)
    tools._resolve_endpoint = lambda app, endpoint: (Endpoint(), app, None, endpoint)
    result = tools.find_existing_create({"app":"dcim","endpoint":"interface-templates","action":"create","data":{"name":"port1","module":7,"device_type":21}})
    assert result.data["id"] == 9


def test_composite_executor_orders_parent_and_all_children_generically():
    writes = []
    def execute(name, arguments):
        writes.append(arguments)
        return ToolResult(ok=True, message="ok", data={"id": len(writes)})
    payload = {
        "object_type":"device-type",
        "manufacturer":"Generic Networks",
        "parent":{"model":"Switch 48X","slug":"switch-48x","u_height":1,"front_image":"not-a-file"},
        "component_templates":{
            "interfaces":[{"name":"port1"},{"name":"port2"}],
            "power-ports":[{"name":"PSU1"}],
            "console-ports":[{"name":"Console"}],
        },
    }
    result = NDXCompositeImporter(lambda arguments: None, execute).run(payload)
    assert result.ok and result.data["templates_processed"] == 4
    assert [item["endpoint"] for item in writes] == ["manufacturers", "device-types", "interface-templates", "interface-templates", "power-port-templates", "console-port-templates"]
    assert "front_image" not in writes[1]["data"]
    assert all(item["data"].get("device_type") == 2 for item in writes[2:])


def test_composite_resolves_component_dependencies_before_writes():
    writes = []
    def execute(name, arguments):
        writes.append(arguments)
        return ToolResult(ok=True, message="ok", data={"id": len(writes)})
    payload = {
        "object_type":"device-type",
        "manufacturer":"Generic",
        "parent":{"model":"Patch","slug":"patch"},
        "component_templates":{
            "interfaces":[{"name":"eth0","type":"1000base-t"}],
            "front-ports":[{"name":"F1","type":"8p8c","rear_port":"R1","rear_port_position":1}],
            "rear-ports":[{"name":"R1","type":"8p8c","positions":1}],
        },
    }
    result = NDXCompositeImporter(lambda arguments: None, execute).run(payload)
    assert result.ok
    assert [item["endpoint"] for item in writes] == ["manufacturers", "device-types", "interface-templates", "rear-port-templates", "front-port-templates"]
    assert writes[-1]["data"]["rear_port"] == 4


def test_composite_generates_required_slugs_for_manufacturer_and_device_type():
    writes = []
    def execute(name, arguments):
        writes.append(arguments)
        return ToolResult(ok=True, message="ok", data={"id": len(writes)})
    payload = {
        "object_type":"device-type",
        "manufacturer":"Cisco Systems, Inc.",
        "parent":{"model":"Catalyst 9200-24P"},
        "component_templates":{"interfaces":[{"name":"GigabitEthernet1/0/1","type":"1000base-t"}]},
    }
    result = NDXCompositeImporter(lambda arguments: None, execute).run(payload)
    assert result.ok
    assert writes[0]["endpoint"] == "manufacturers"
    assert writes[0]["data"]["slug"] == "cisco-systems-inc"
    assert writes[1]["endpoint"] == "device-types"
    assert writes[1]["data"]["slug"] == "catalyst-9200-24p"


def test_runtime_contains_no_vendor_specific_rules():
    root = Path(__file__).parents[1] / "src" / "netwaive"
    runtime = "\n".join(path.read_text() for path in root.glob("*.py"))
    for vendor_literal in ("Cisco Systems", "Alcatel-Lucent Enterprise", '"ale"', '"alcatel"'):
        assert vendor_literal not in runtime
