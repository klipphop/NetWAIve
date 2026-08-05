import json
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from netwaive.agent import NetBoxAgent
from netwaive.config import Settings
from netwaive.models import (
    GetEndpointSchemaArgs,
    NetBoxReadArgs,
    NetBoxWriteArgs,
    PendingToolCall,
    ToolResult,
)
from netwaive.prompt import SYSTEM_PROMPT
from netwaive.tools import NetBoxTools


def settings():
    return Settings(
        netbox_url="https://netbox.invalid",
        netbox_token=SecretStr("token"),
        llm_base_url="https://llm.invalid/v1",
        llm_api_key=SecretStr("key"),
        llm_model="test-model",
    )


class Message:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []

    def model_dump(self, exclude_none=True):
        value = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            value["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.function.name, "arguments": call.function.arguments},
                }
                for call in self.tool_calls
            ]
        return {key: item for key, item in value.items() if item is not None}


class FakeClient:
    def __init__(self, messages):
        self.responses = iter(messages)
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=next(self.responses))])


class FakeTools:
    ARG_MODELS = {
        "netbox_read": NetBoxReadArgs,
        "netbox_write": NetBoxWriteArgs,
        "get_endpoint_schema": GetEndpointSchemaArgs,
    }
    MUTATING_TOOLS = {"netbox_write"}

    def tool_schemas(self):
        return [{"type": "function", "function": {"name": name, "parameters": model.model_json_schema()}} for name, model in self.ARG_MODELS.items()]

    def find_existing_create(self, arguments) -> ToolResult | None:
        return None

    def execute(self, name, arguments):
        if name == "netbox_read":
            return ToolResult(
                ok=True,
                message="Lecture exécutée",
                data=[
                    {"id": 1, "name": "SW-01"},
                    {"id": 21, "name": "1/1/21"},
                    {"id": 22, "name": "1/1/22"},
                    {"id": 23, "name": "1/1/23"},
                    {"id": 24, "name": "1/1/24"},
                ],
            )
        return ToolResult(ok=True, message=f"{name} exécuté", data=arguments)


def tool_call(name, arguments, call_id="call-1"):
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=json.dumps(arguments)),
    )


def test_plan_sanitization_deduplicates_and_rejects_unknown_variables():
    call = PendingToolCall(id="site", name="netbox_write", arguments={"app":"dcim","endpoint":"sites","action":"create","data":{"name":"LAB"}})
    clean, errors = NetBoxAgent._sanitize_plan([call, call])
    assert len(clean) == 1 and not errors
    bad = PendingToolCall(id="device", name="netbox_write", arguments={"app":"dcim","endpoint":"devices","action":"create","data":{"site":"${existing?.id}"}})
    _, errors = NetBoxAgent._sanitize_plan([bad])
    assert errors


def test_corrupted_reference_suffixes_are_canonicalized_to_parent_id():
    parent = PendingToolCall(id="call_ABC123", name="netbox_write", arguments={
        "app": "dcim", "endpoint": "manufacturers", "action": "create", "data": {"name": "Acme"},
    })
    child_short = PendingToolCall(id="child-short", name="netbox_write", arguments={
        "app": "dcim", "endpoint": "device-types", "action": "create",
        "data": {"model": "X1", "manufacturer": "${call_ABC123-type}"},
    })
    child_path = PendingToolCall(id="child-path", name="netbox_write", arguments={
        "app": "dcim", "endpoint": "device-types", "action": "create",
        "data": {"model": "X2", "manufacturer": "${call_ABC123.data.id-device}"},
    })
    clean, errors = NetBoxAgent._sanitize_plan([child_path, parent, child_short])
    assert errors == []
    by_id = {call.id: call for call in clean}
    expected = "${call_ABC123.data.id}"
    assert by_id["child-short"].arguments["data"]["manufacturer"] == expected
    assert by_id["child-path"].arguments["data"]["manufacturer"] == expected
    outputs = {"call_ABC123": {"ok": True, "data": {"id": 77}}}
    assert NetBoxAgent._resolve_references("${call_ABC123-type}", outputs) == 77
    assert NetBoxAgent._resolve_references("${call_ABC123.data.id-device}", outputs) == 77
    assert NetBoxAgent._resolve_references("${call_1-type}", outputs) == 77

    exact_outputs = {
        "call_ABC123": {"ok": True, "data": {"id": 77}},
        "call_ABC123-type": {"ok": True, "data": {"id": 88}},
    }
    assert NetBoxAgent._resolve_references("${call_ABC123-type.data.id}", exact_outputs) == 88

    ordinal_child = PendingToolCall(id="ordinal-child", name="netbox_write", arguments={
        "app": "dcim", "endpoint": "device-types", "action": "create",
        "data": {"model": "O1", "manufacturer": "${call_2.data.id}"},
    })
    ordinal_parent = PendingToolCall(id="ordinal-parent", name="netbox_write", arguments={
        "app": "dcim", "endpoint": "manufacturers", "action": "create", "data": {"name": "Ordinal"},
    })
    ordinal_plan, ordinal_errors = NetBoxAgent._sanitize_plan([ordinal_child, ordinal_parent])
    assert ordinal_errors == []
    assert [call.id for call in ordinal_plan] == ["ordinal-parent", "ordinal-child"]
    assert ordinal_plan[1].arguments["data"]["manufacturer"] == "${ordinal-parent.data.id}"

    class OrdinalTools(FakeTools):
        def __init__(self):
            self.received = []
        def execute(self, name, arguments):
            self.received.append(arguments)
            if arguments.get("endpoint") == "manufacturers":
                return ToolResult(ok=True, message="parent", data={"id": 91})
            return ToolResult(ok=True, message="child", data=arguments)

    ordinal_tools = OrdinalTools()
    confirmed = NetBoxAgent(settings(), tools=ordinal_tools, client=FakeClient([])).confirm(
        "confirme", [ordinal_child, ordinal_parent]
    )
    assert all(result.ok for result in confirmed.tool_results)
    assert ordinal_tools.received[1]["data"]["manufacturer"] == 91
    assert isinstance(ordinal_tools.received[1]["data"]["manufacturer"], int)


def test_netbox_errors_preserve_native_payload():
    message = NetBoxTools._friendly_error("Related object not found: device role 'Switch'")
    assert message == "Related object not found: device role 'Switch'"


def test_symbolic_reference_resolves_call_aliases_and_result_ids():
    outputs = {"toolu_abc": {"ok": True, "data": {"id": 12}}}
    assert NetBoxAgent._resolve_reference("toolu_abc.data.id", outputs) == 12
    assert NetBoxAgent._resolve_reference("toolu_abc.id", outputs) == 12
    with pytest.raises(ValueError, match="inconnue|unknown"):
        NetBoxAgent._resolve_reference("call_o1hFJh1F074DRdjM1l4v8ACm.data.id", outputs)
    with pytest.raises(ValueError, match="inconnue|unknown"):
        NetBoxAgent._resolve_reference("call_unknown.data.id", {**outputs, "other": {"data": {"id": 13}}})


def test_typed_reference_resolves_list_index_and_rejects_empty_values():
    outputs = {"lookup": {"ok": True, "data": {"candidates": [{"slug": "switch"}]}}}
    assert NetBoxAgent._resolve_references("${lookup.data.candidates[0].slug}", outputs) == "switch"
    assert NetBoxAgent._resolve_references("${call_1.data.candidates[0].slug}", outputs) == "switch"

    with pytest.raises(ValueError, match="vide|empty|invalide|invalid"):
        NetBoxAgent._resolve_references("${lookup.data}", {"lookup": {"ok": True, "data": {}}})
    with pytest.raises(ValueError, match="introuvable|unknown"):
        NetBoxAgent._resolve_references("${lookup.data.missing}", {"lookup": {"ok": True, "data": {}}})


def test_confirm_pipeline_resolves_generated_id_as_integer_and_stops_on_empty_parent():
    class PipelineTools(FakeTools):
        def __init__(self, empty=False):
            self.empty = empty
            self.received = []
        def find_existing_create(self, arguments): return None
        def execute(self, name, arguments):
            self.received.append(arguments)
            if len(self.received) == 1:
                return ToolResult(ok=True, message="parent", data={} if self.empty else {"id": 42})
            return ToolResult(ok=True, message="child", data=arguments)

    pending = [
        PendingToolCall(id="parent", name="netbox_write", arguments={"app":"dcim","endpoint":"manufacturers","action":"create","data":{"name":"Acme"}}),
        PendingToolCall(id="child", name="netbox_write", arguments={"app":"dcim","endpoint":"device-types","action":"create","data":{"model":"X1","manufacturer":"${parent.data.id}"}}),
    ]
    tools = PipelineTools()
    result = NetBoxAgent(settings(), tools=tools, client=FakeClient([])).confirm("confirme", pending)
    assert result.tool_results[-1].ok
    assert tools.received[1]["data"]["manufacturer"] == 42
    assert isinstance(tools.received[1]["data"]["manufacturer"], int)

    empty_tools = PipelineTools(empty=True)
    failed = NetBoxAgent(settings(), tools=empty_tools, client=FakeClient([])).confirm("confirme", pending)
    assert not failed.tool_results[-1].ok
    assert len(empty_tools.received) == 1


def test_parent_dependencies_are_ordered_before_device_creation():
    manufacturer = PendingToolCall(id="manufacturer", name="netbox_write", arguments={"app":"dcim","endpoint":"manufacturers","action":"create","data":{"name":"Acme"}})
    device_type = PendingToolCall(id="type", name="netbox_write", arguments={"app":"dcim","endpoint":"device-types","action":"create","data":{"model":"X1","manufacturer":"${manufacturer.data.id}"}})
    device = PendingToolCall(id="device", name="netbox_write", arguments={"app":"dcim","endpoint":"devices","action":"create","data":{"name":"sw-01","device_type":"${type.data.id}"}})
    ordered = NetBoxAgent._order_pending([device, device_type, manufacturer])
    assert [call.id for call in ordered] == ["manufacturer", "type", "device"]


def test_plan_rejects_duplicate_step_ids_and_dependency_cycles():
    duplicate = [
        PendingToolCall(id="same", name="netbox_write", arguments={"app":"dcim","endpoint":"sites","action":"create","data":{"name":"A"}}),
        PendingToolCall(id="same", name="netbox_write", arguments={"app":"dcim","endpoint":"sites","action":"create","data":{"name":"B"}}),
    ]
    _, duplicate_errors = NetBoxAgent._sanitize_plan(duplicate)
    assert any("dupliqu" in error for error in duplicate_errors)

    cycle = [
        PendingToolCall(id="a", name="netbox_write", arguments={"app":"dcim","endpoint":"sites","action":"create","data":{"name":"${b.data.name}"}}),
        PendingToolCall(id="b", name="netbox_write", arguments={"app":"dcim","endpoint":"sites","action":"create","data":{"name":"${a.data.name}"}}),
    ]
    _, cycle_errors = NetBoxAgent._sanitize_plan(cycle)
    assert any("Cycle" in error for error in cycle_errors)

    unknown = [PendingToolCall(id="x", name="netbox_write", arguments={"app":"dcim","endpoint":"sites","action":"create","data":{"name":"${call_missing.data.name}"}})]
    _, unknown_errors = NetBoxAgent._sanitize_plan(unknown)
    assert any("inconnue" in error for error in unknown_errors)

    class NoExecute(FakeTools):
        def __init__(self): self.count = 0
        def execute(self, name, arguments):
            self.count += 1
            return ToolResult(ok=True, message="unexpected")
    tools = NoExecute()
    plan = [
        PendingToolCall(id="safe", name="netbox_write", arguments={"app":"dcim","endpoint":"sites","action":"create","data":{"name":"SAFE"}}),
        unknown[0],
    ]
    refused = NetBoxAgent(settings(), tools=tools, client=FakeClient([])).confirm("confirme", plan)
    assert tools.count == 0
    assert refused.tool_results == []


def test_structured_yaml_and_ascii_inputs_are_treated_as_creation_plans():
    assert NetBoxAgent._is_structured_plan("sites:\n  - name: LAB-PARIS")
    assert NetBoxAgent._is_structured_plan("├── SRV-PROXMOX\n└── SW-TOR-01")


def test_partial_failure_retains_unexecuted_calls_for_completion():
    class PartialTools(FakeTools):
        def __init__(self): self.count = 0
        def execute(self, name, arguments):
            self.count += 1
            return ToolResult(ok=self.count != 2, message="OK" if self.count != 2 else "failed", data=arguments)
    pending = [PendingToolCall(id=f"c{i}", name="netbox_write", arguments={"app":"dcim","endpoint":"sites","action":"create","data":{"name":f"S{i}"}}) for i in range(3)]
    result = NetBoxAgent(settings(), tools=PartialTools(), client=FakeClient([])).confirm("Créer les sites", pending)
    assert len(result.pending_confirmation) == 1
    assert result.pending_confirmation[0].id == "c2"
    assert "finalisées" in result.message


def test_end_to_end_site_and_device_are_one_pending_plan_without_transition():
    read_site = tool_call("netbox_read", {"app":"dcim","endpoint":"sites","method":"filter","kwargs":{"name":"DC-PARIS-01"}}, "read-site")
    create_site = tool_call("netbox_write", {"app":"dcim","endpoint":"sites","action":"create","data":{"name":"DC-PARIS-01"}}, "create-site")
    read_device = tool_call("netbox_read", {"app":"dcim","endpoint":"devices","method":"filter","kwargs":{"name":"SW-TEST-99"}}, "read-device")
    create_device = tool_call("netbox_write", {"app":"dcim","endpoint":"devices","action":"create","data":{"name":"SW-TEST-99","site":"${create-site.data.id}"}}, "create-device")
    client = FakeClient([
        Message(tool_calls=[read_site]),
        Message(tool_calls=[create_site]),
        Message("Je vérifie le prérequis."),
        Message(tool_calls=[read_device]),
        Message(tool_calls=[create_device]),
        Message("Plan complet."),
    ])
    result = NetBoxAgent(settings(), tools=FakeTools(), client=client).run("Crée le switch SW-TEST-99 dans le site DC-PARIS-01")
    assert [call.id for call in result.pending_confirmation] == ["create-site", "create-device"]
    assert "DC-PARIS-01" in result.message and "SW-TEST-99" in result.message
    assert "Je vérifie" not in result.message
    assert len(client.calls) == 6


def test_openapi_preflight_returns_missing_fields_and_enum_choices():
    class SchemaTools(NetBoxTools):
        def get_endpoint_schema(self, args):
            return ToolResult(ok=True, message="schema", data={
                "required_fields": ["termination_type", "type"],
                "writable_fields": {"type": {"enum": ["cat6a", "fiber-os2"]}, "termination_type": {"enum": ["interface", "consoleport"]}},
            })
    result = SchemaTools(settings()).validate_write_payload({"app": "any", "endpoint": "objects", "action": "create", "data": {}})
    assert result is not None and result.ok is False
    assert result.data["missing_fields"][0]["field"] == "termination_type"
    assert result.data["missing_fields"][1]["choices"] == ["cat6a", "fiber-os2"]


def test_duplicate_termination_error_is_actionable():
    message = NetBoxTools._friendly_error("Duplicate termination found for dcim.interface 42")
    assert "déjà câblée" in message
    assert "dcim.interface 42" in message


def test_preflight_collision_stops_a_pending_write():
    class CollisionTools(FakeTools):
        def preflight_termination_collisions(self, arguments):
            return ToolResult(ok=False, message="L’interface xe-0/0/1 sur l’équipement SW-01 est déjà câblée (Câble #12). Veuillez choisir une autre interface ou déconnecter l’existante.")
    read = tool_call("netbox_read", {"app":"dcim","endpoint":"cables","method":"filter","kwargs":{}}, "read-cables")
    write = tool_call("netbox_write", {"app":"dcim","endpoint":"cables","action":"create","data":{"label":"ID001"}}, "create-cable")
    agent = NetBoxAgent(settings(), tools=CollisionTools(), client=FakeClient([Message(tool_calls=[read]), Message(tool_calls=[write]), Message("Conflit détecté.")]))
    result = agent.run("Crée le câble ID001")
    assert result.pending_confirmation == []
    assert any("déjà câblée" in item.message for item in result.tool_results)


def test_ndx_catalog_search_returns_exact_candidate(monkeypatch):
    class Response:
        status_code = 200
        ok = True
        def raise_for_status(self): pass
        def json(self):
            return [{"vendor_name":"Cisco Systems","manufacturer":"Cisco","model":"Catalyst 9300-24P","part_number":"C9300-24P","slug":"cisco-c9300-24p","type":"device-type","component_templates":{"interfaces":[{"name":"GigabitEthernet1/0/1","type":"1000base-t"}]}}]
    monkeypatch.setattr("netwaive.ndx.requests.get", lambda url, timeout: Response())
    monkeypatch.setattr(NetBoxTools, "_existing_ndx_parent", lambda self, model, object_type, manufacturer: None)
    result = NetBoxTools(settings()).netbox_read(NetBoxReadArgs(app="ndx", endpoint="catalog", method="get", kwargs={"query":"C9300-24P"}))
    assert result.ok
    assert result.data["parent"]["model"] == "Catalyst 9300-24P"
    assert result.data["parent"]["slug"] == "cisco-c9300-24p"


def test_ndx_catalog_search_returns_ambiguous_candidates(monkeypatch):
    class Response:
        status_code = 200
        ok = True
        def raise_for_status(self): pass
        def json(self): return [{"model":"Catalyst 9200-24P"},{"model":"Catalyst 9200-24T"}]
    monkeypatch.setattr("netwaive.ndx.requests.get", lambda url, timeout: Response())
    result = NetBoxTools(settings()).netbox_read(NetBoxReadArgs(app="ndx", endpoint="catalog", method="get", kwargs={"query":"Catalyst 9200"}))
    assert len(result.data["candidates"]) == 2


def test_composite_ndx_import_is_one_pending_action_and_executes_all_templates():
    class CompositeTools(FakeTools):
        def execute(self, name, arguments):
            if name == "netbox_read" and arguments.get("app") == "ndx":
                return ToolResult(ok=True, message="NDX chargé", data={
                    "object_type": "device-type",
                    "parent": {"model":"Catalyst 9300-24P","slug":"c9300-24p","u_height":1},
                    "manufacturer":"Cisco Systems",
                    "component_templates":{"interfaces":[{"name":f"Gi1/0/{i}"} for i in range(1,28)],"console-ports":[{"name":"Console"}],"power-ports":[]},
                })
            return super().execute(name, arguments)
        def import_ndx_object(self, arguments):
            payload = arguments["payload"]
            assert payload["object_type"] == "device-type"
            assert len(payload["component_templates"]["interfaces"]) == 27
            return ToolResult(ok=True, message="Import NDX terminé", data={"templates_processed":27})
    read = tool_call("netbox_read", {"app":"ndx","endpoint":"spec","method":"get","kwargs":{"model":"C9300-24P"}}, "ndx-read")
    client = FakeClient([Message(tool_calls=[read])])
    result = NetBoxAgent(settings(), tools=CompositeTools(), client=client).run("Importe C9300-24P depuis NDX")
    assert len(client.calls) == 1
    assert len(result.pending_confirmation) == 1
    assert result.pending_confirmation[0].name == "import_ndx_object"
    assert "Import NDX" in result.message


def test_direct_module_type_create_is_bound_to_complete_ndx_composite():
    class ModuleTools(FakeTools):
        def prepare_ndx_object(self, data, object_type):
            assert object_type == "module-type"
            return ToolResult(ok=True, message="spec", data={"composite":{"type":"import_ndx_object","payload":{
                "object_type":"module-type",
                "manufacturer":"Generic",
                "parent":{"model":"PSU-1000","part_number":"P-1000"},
                "component_templates":{"power-ports":[{"name":"Power Input"}]},
            }}})
    write = tool_call("netbox_write", {"app":"dcim","endpoint":"module-types","action":"create","data":{"model":"PSU-1000"}}, "create-module-type")
    result = NetBoxAgent(settings(), tools=ModuleTools(), client=FakeClient([Message(tool_calls=[write]), Message("Plan prêt"), Message("Plan final")])).run("Crée PSU-1000")
    assert len(result.pending_confirmation) == 1
    assert result.pending_confirmation[0].name == "import_ndx_object"
    assert result.pending_confirmation[0].arguments["payload"]["component_templates"]["power-ports"]
    assert "ModuleType" in result.message


def test_only_three_universal_tools_are_exposed():
    assert set(FakeTools.ARG_MODELS) == {"netbox_read", "netbox_write", "get_endpoint_schema"}


def test_read_accepts_dynamic_filters():
    args = NetBoxReadArgs.model_validate({
        "app": "ipam", "endpoint": "vlans", "method": "filter", "site": "fr01"
    })
    assert args.merged_kwargs() == {"site": "fr01"}


def test_theoretical_answer_without_tool():
    agent = NetBoxAgent(settings(), tools=FakeTools(), client=FakeClient([Message("Réponse BGP")]))
    result = agent.run("Explique BGP")
    assert result.message == "Réponse BGP"
    assert result.pending_confirmation == []


def test_system_prompt_is_intent_only_and_delegates_backend_fallbacks():
    prompt = SYSTEM_PROMPT.casefold()
    assert len(SYSTEM_PROMPT) < 4000
    assert "intention" in prompt
    assert "runtime python" in prompt
    assert "plan" in prompt
    assert "zero-ask completion" in prompt
    assert "generic" in prompt
    assert "plan netbox brut" in prompt
    assert "ne demande jamais un slug" in prompt
    assert "champ métier obligatoire" in prompt
    assert "quantité explicite de composants" in prompt
    assert "fabricant par défaut" in prompt
    assert "exactement `generic`" in prompt
    assert "unknown" in prompt and "inconnu" in prompt
    for forbidden in (
        "avant chaque mutation",
        "vérifier l'existence",
        "vérifie l'absence",
        "absence de doublon",
        "device role",
        "device-role",
        "déduis le rôle",
    ):
        assert forbidden not in prompt


def test_missing_required_business_value_asks_one_question_without_pending():
    class MissingBusinessTools(FakeTools):
        def enrich_write_arguments(self, arguments):
            return arguments
        def validate_write_payload(self, arguments):
            return ToolResult(
                ok=False,
                message="Valeurs requises manquantes",
                data={"missing_fields": [{"field": "name", "choices": []}]},
            )

    write = tool_call("netbox_write", {
        "app": "dcim", "endpoint": "sites", "action": "create", "data": {},
    }, "create-site")
    result = NetBoxAgent(
        settings(), tools=MissingBusinessTools(),
        client=FakeClient([Message(tool_calls=[write]), Message("Quel nom de site souhaitez-vous utiliser ?")]),
    ).run("Crée un site")
    assert result.pending_confirmation == []
    assert result.message == "Quel nom de site souhaitez-vous utiliser ?"
    assert result.message.count("?") == 1


def test_clear_create_intent_produces_direct_pending_without_read_or_question():
    write = tool_call("netbox_write", {
        "app": "dcim", "endpoint": "sites", "action": "create", "data": {"name": "LAB-PARIS-02"}
    }, "create-site")
    messages = [Message(tool_calls=[write]), Message("Plan complet."), Message("Plan final.")]
    client = FakeClient(messages)
    result = NetBoxAgent(settings(), tools=FakeTools(), client=client).run("Crée le site LAB-PARIS-02")
    assert len(result.pending_confirmation) == 1
    assert result.pending_confirmation[0].id == "create-site"
    assert all(call.function.name != "netbox_read" for response in messages for call in response.tool_calls)
    assert result.message.count("?") == 1
    assert "Confirmez-vous" in result.message


def test_device_intent_auto_chains_raw_dependencies_without_question():
    class RawFallbackTools(FakeTools):
        def prepare_ndx_object(self, data, object_type):
            return ToolResult(ok=True, message="fallback", data={
                "raw_fallback": {
                    "model": data.get("model"),
                    "manufacturer": data.get("manufacturer") or "Generic",
                    "component_templates": data.get("components") or {},
                }
            })

    write = tool_call("netbox_write", {
        "app": "dcim", "endpoint": "devices", "action": "create", "data": {
            "name": "SW-CUSTOM-01", "model": "CUSTOM-48P", "site": "LAB-PARIS",
            "components": {"interfaces": [{"name": "eth0", "type": "1000base-t"}]},
        }
    }, "create-device")
    result = NetBoxAgent(
        settings(),
        tools=RawFallbackTools(),
        client=FakeClient([Message(tool_calls=[write]), Message("Plan complet."), Message("Plan final.")]),
    ).run("Crée SW-CUSTOM-01 modèle CUSTOM-48P sur LAB-PARIS")

    calls = result.pending_confirmation
    endpoints = [call.arguments["endpoint"] for call in calls]
    assert set(endpoints) == {"manufacturers", "device-types", "interface-templates", "sites", "devices"}
    assert endpoints.index("manufacturers") < endpoints.index("device-types") < endpoints.index("interface-templates") < endpoints.index("devices")
    assert endpoints.index("sites") < endpoints.index("devices")
    by_endpoint = {call.arguments["endpoint"]: call for call in calls}
    assert by_endpoint["manufacturers"].arguments["data"]["name"] == "Generic"
    assert by_endpoint["device-types"].arguments["data"]["manufacturer"] == "${create-device-manufacturer.data.id}"
    assert by_endpoint["interface-templates"].arguments["data"]["device_type"] == "${create-device-type.data.id}"
    assert by_endpoint["devices"].arguments["data"]["device_type"] == "${create-device-type.data.id}"
    assert by_endpoint["devices"].arguments["data"]["site"] == "${create-device-site.data.id}"
    assert "?" in result.message  # confirmation globale uniquement


def test_raw_component_quantity_expands_to_distinct_pending_steps():
    agent = NetBoxAgent(settings(), tools=FakeTools(), client=FakeClient([]))
    calls, error, _ = agent._raw_parent_calls("power-device", {
        "model": "PDU-8E",
        "manufacturer": "Generic",
        "component_templates": {
            "power-ports": [{"name": "Power Port", "type": "type-e", "quantity": 8}],
        },
    }, "device-type")
    assert error is None
    components = [call for call in calls if call.arguments.get("endpoint") == "power-port-templates"]
    assert len(components) == 8
    assert [call.arguments["data"]["name"] for call in components] == [f"Power Port {i}" for i in range(1, 9)]
    assert len({call.id for call in components}) == 8
    assert all(call.arguments["data"]["device_type"] == "${power-device-type.data.id}" for call in components)

    direct = PendingToolCall(id="direct-power", name="netbox_write", arguments={
        "app": "dcim", "endpoint": "power-port-templates", "action": "create",
        "data": {"name": "Input", "type": "type-e", "count": 8, "device_type": 99},
    })
    direct_calls, direct_errors, _ = agent._prepare_pending_plan([direct])
    assert direct_errors == []
    assert len(direct_calls) == 8
    assert [call.arguments["data"]["name"] for call in direct_calls] == [f"Input {index}" for index in range(1, 9)]
    for invalid_quantity in (0, 513, 1.5, "eight", True):
        with pytest.raises(ValueError, match="[Qq]uantité"):
            agent._expand_component_spec({"name": "Invalid", "quantity": invalid_quantity})


def test_module_type_missing_manufacturer_uses_generic():
    agent = NetBoxAgent(settings(), tools=FakeTools(), client=FakeClient([]))
    calls, error, _ = agent._raw_parent_calls("module-device", {"model": "MOD-1", "component_templates": {}}, "module-type")
    assert error is None
    manufacturer = next(call for call in calls if call.arguments["endpoint"] == "manufacturers")
    assert manufacturer.arguments["data"]["name"] == "Generic"


def test_plan_time_lookup_reuses_existing_manufacturer_and_site():
    class ExistingTools(FakeTools):
        def prepare_ndx_object(self, data, object_type):
            return ToolResult(ok=True, message="fallback", data={"raw_fallback": {
                "model": data.get("model"), "manufacturer": data.get("manufacturer") or "Generic",
                "component_templates": {},
            }})
        def find_existing_create(self, arguments):
            endpoint = arguments.get("endpoint")
            if endpoint == "manufacturers":
                return ToolResult(ok=True, message="existing", data={"id": 10, "name": "Generic"})
            if endpoint == "sites":
                return ToolResult(ok=True, message="existing", data={"id": 20, "name": "LAB"})
            return None

    write = tool_call("netbox_write", {
        "app": "dcim", "endpoint": "devices", "action": "create", "data": {
            "name": "SW-LOOKUP", "model": "CUSTOM", "manufacturer": "Generic", "site": "LAB",
        },
    }, "lookup-device")
    result = NetBoxAgent(
        settings(), tools=ExistingTools(),
        client=FakeClient([Message(tool_calls=[write]), Message("Plan complet."), Message("Plan final.")]),
    ).run("Crée SW-LOOKUP modèle CUSTOM sur LAB")
    calls = result.pending_confirmation
    endpoints = [call.arguments.get("endpoint") for call in calls]
    assert "manufacturers" not in endpoints
    assert "sites" not in endpoints
    device_type = next(call for call in calls if call.arguments.get("endpoint") == "device-types")
    device = next(call for call in calls if call.arguments.get("endpoint") == "devices")
    assert device_type.arguments["data"]["manufacturer"] == 10
    assert device.arguments["data"]["site"] == 20
    assert isinstance(device_type.arguments["data"]["manufacturer"], int)
    assert isinstance(device.arguments["data"]["site"], int)

    existing_agent = NetBoxAgent(settings(), tools=ExistingTools(), client=FakeClient([]))
    existing_site = PendingToolCall(id="existing-site", name="netbox_write", arguments={
        "app": "dcim", "endpoint": "sites", "action": "create", "data": {"name": "LAB"},
    })
    remaining, lookup_errors, reused = existing_agent._prepare_pending_plan([existing_site])
    assert remaining == [] and lookup_errors == [] and len(reused) == 1

    class BrokenLookupTools(FakeTools):
        def find_existing_create(self, arguments):
            raise RuntimeError("SECRET")

    blocked, lookup_errors, _ = NetBoxAgent(
        settings(), tools=BrokenLookupTools(), client=FakeClient([]),
    )._prepare_pending_plan([existing_site])
    assert blocked == []
    assert lookup_errors == ["Vérification NetBox impossible ; plan bloqué par sécurité."]
    assert "SECRET" not in lookup_errors[0]

    existing_write = tool_call("netbox_write", {
        "app": "dcim", "endpoint": "sites", "action": "create", "data": {"name": "LAB"},
    }, "site-existing")
    existing_result = NetBoxAgent(
        settings(), tools=ExistingTools(),
        client=FakeClient([Message(tool_calls=[existing_write]), Message("Plan complet."), Message("Plan final.")]),
    ).run("Crée le site LAB")
    assert existing_result.pending_confirmation == []
    assert "existe" in existing_result.message
    assert "Confirmez" not in existing_result.message


def test_device_intent_chains_exact_ndx_import_to_device():
    class ExactNDXTools(FakeTools):
        def prepare_ndx_object(self, data, object_type):
            return ToolResult(ok=True, message="exact", data={
                "composite": {"payload": {"object_type": "device-type", "manufacturer": "Acme", "parent": {"model": data["model"]}, "component_templates": {}}}
            })

    write = tool_call("netbox_write", {
        "app": "dcim", "endpoint": "devices", "action": "create",
        "data": {"name": "SW-NDX-01", "model": "NDX-48P"},
    }, "create-ndx-device")
    result = NetBoxAgent(
        settings(), tools=ExactNDXTools(),
        client=FakeClient([Message(tool_calls=[write]), Message("Plan complet."), Message("Plan final.")]),
    ).run("Crée SW-NDX-01 modèle NDX-48P")
    assert [call.name for call in result.pending_confirmation] == ["import_ndx_object", "netbox_write"]
    assert result.pending_confirmation[-1].arguments["data"]["device_type"] == "${create-ndx-device-type.data.id}"


def test_direct_create_confirmation_deduplicates_and_fails_closed_on_lookup_error():
    pending = [PendingToolCall(id="site", name="netbox_write", arguments={
        "app": "dcim", "endpoint": "sites", "action": "create", "data": {"name": "LAB-PARIS-02"}
    })]

    class ConfirmTools(FakeTools):
        def __init__(self, fail=False):
            self.fail = fail
            self.executed = 0
        def find_existing_create(self, arguments):
            if self.fail:
                raise RuntimeError("backend secret")
            return ToolResult(ok=True, message="Site déjà présent", data={"name": "LAB-PARIS-02"})
        def execute(self, name, arguments):
            self.executed += 1
            return ToolResult(ok=True, message="created")

    existing_tools = ConfirmTools()
    existing = NetBoxAgent(settings(), tools=existing_tools, client=FakeClient([])).confirm("confirme", pending)
    assert existing.tool_results[0].ok
    assert existing_tools.executed == 0

    failed_tools = ConfirmTools(fail=True)
    failed = NetBoxAgent(settings(), tools=failed_tools, client=FakeClient([])).confirm("confirme", pending)
    assert not failed.tool_results[0].ok
    assert failed_tools.executed == 0
    assert "backend secret" not in failed.tool_results[0].message


def test_write_request_cannot_return_a_prose_confirmation_without_live_plan():
    read = tool_call("netbox_read", {
        "app": "dcim", "endpoint": "sites", "method": "filter", "kwargs": {"name": "LAB-PARIS-01"}
    }, "read-site")
    client = FakeClient([
        Message("Modifications en attente de votre validation : Confirmez-vous ?"),
        Message(tool_calls=[read]),
        Message("Le site et les VLANs existent déjà, tout est en place !"),
    ])
    result = NetBoxAgent(settings(), tools=FakeTools(), client=client).run("Crée le site LAB-PARIS-01")
    assert result.pending_confirmation == []
    assert result.message == "Le site et les VLANs existent déjà, tout est en place !"
    assert len(client.calls) == 3


def test_transitional_text_is_not_returned_before_tool_chain_and_pending_plan():
    read_prefix = tool_call("netbox_read", {
        "app": "ipam", "endpoint": "prefixes", "method": "filter", "kwargs": {"prefix": "10.50.0.0/24"}
    }, "read-prefix")
    write_prefix = tool_call("netbox_write", {
        "app": "ipam", "endpoint": "prefixes", "action": "create", "data": {"prefix": "10.50.0.0/24"}
    }, "create-prefix")
    client = FakeClient([
        Message("Compris. Je poursuis automatiquement la création."),
        Message("Je prépare les actions nécessaires."),
        Message(tool_calls=[read_prefix]),
        Message(tool_calls=[write_prefix]),
        Message("Plan prêt."),
        Message("Plan final."),
    ])
    result = NetBoxAgent(settings(), tools=FakeTools(), client=client).run(
        "ajouter un subnet 10.50.0.0/24 et l’affecter au vlan 500"
    )
    assert len(client.calls) == 6
    assert len(result.pending_confirmation) == 1
    assert "Création: prefixes '10.50.0.0/24'" in result.message
    assert "Compris" not in result.message


def test_universal_write_requires_confirmation():
    call = tool_call("netbox_write", {
        "app": "dcim", "endpoint": "devices", "action": "create", "data": {"name": "sw-02"}
    })
    read = tool_call("netbox_read", {
        "app": "dcim", "endpoint": "devices", "method": "filter", "kwargs": {"name": "sw-02"}
    }, "read-device")
    agent = NetBoxAgent(settings(), tools=FakeTools(), client=FakeClient([
        Message(tool_calls=[read]), Message(tool_calls=[call]), Message("Plan complet"), Message("Plan final")
    ]))
    result = agent.run("Crée sw-02")
    assert result.pending_confirmation[0].name == "netbox_write"
    assert "Création: devices 'sw-02'" in result.message
    assert "dcim/devices" not in result.message


def test_confirm_executes_exact_call_and_closes_without_replanning():
    pending = [PendingToolCall(
        id="call-1",
        name="netbox_write",
        arguments={"app": "dcim", "endpoint": "devices", "action": "create", "data": {"name": "sw-02"}},
    )]
    client = FakeClient([Message(tool_calls=[tool_call("netbox_write", {"app": "dcim", "endpoint": "devices", "action": "create", "data": {"name": "duplicate"}})])])
    agent = NetBoxAgent(settings(), tools=FakeTools(), client=client)
    result = agent.confirm("Crée sw-02", pending)
    assert "exécutées avec succès" in result.message
    assert result.pending_confirmation == []
    assert result.tool_results[0].data["app"] == "dcim"
    assert client.calls == []


def test_confirmation_success_summary_matches_the_user_language():
    pending = [PendingToolCall(
        id="call-1",
        name="netbox_write",
        arguments={"app": "dcim", "endpoint": "sites", "action": "create", "data": {"name": "HomeLab"}},
    )]
    result = NetBoxAgent(settings(), tools=FakeTools(), client=FakeClient([])).confirm("Create site HomeLab", pending)
    assert "approved operation(s) were executed successfully" in result.message
    assert "configuration is now in place" in result.message


def test_composite_order_produces_one_global_confirmation():
    create = tool_call(
        "netbox_write",
        {"app": "dcim", "endpoint": "interfaces", "action": "create", "data": {"device": 1, "name": "po1", "type": "lag"}},
        "create-lag",
    )
    updates = [
        tool_call(
            "netbox_write",
            {"app": "dcim", "endpoint": "interfaces", "action": "update", "data": {"id": port, "lag": "${create-lag.data.id}"}},
            f"update-{port}",
        )
        for port in (21, 22, 23, 24)
    ]
    read_interfaces = tool_call(
        "netbox_read",
        {"app": "dcim", "endpoint": "interfaces", "method": "filter", "kwargs": {"device": "SW-01"}},
        "read-interfaces",
    )
    client = FakeClient([
        Message(tool_calls=[read_interfaces]),
        Message(tool_calls=[create]),
        Message(tool_calls=updates),
        Message("Plan complet"),
        Message("Plan final"),
    ])
    result = NetBoxAgent(settings(), tools=FakeTools(), client=client).run("Crée po1 et attache les quatre interfaces")
    assert len(result.pending_confirmation) == 5
    assert result.message.startswith("Modifications en attente de votre validation")
    assert "Création: interfaces 'po1'" in result.message
    assert result.message.count("Mise à jour: interfaces") == 4
    assert "dcim/interfaces" not in result.message
    assert "device=" not in result.message
    assert "${" not in result.message
    assert result.pending_confirmation[-1].arguments["data"]["lag"] == "${create-lag.data.id}"


def test_confirmation_displays_every_payload_parameter_and_known_label():
    pending = [PendingToolCall(
        id="create-vlan",
        name="netbox_write",
        arguments={
            "app": "ipam",
            "endpoint": "vlans",
            "action": "create",
            "data": {"vid": 250, "name": "DMZ", "site": 2, "api_key": "secret"},
        },
    )]
    outputs = {"read-site": {"data": [{"id": 2, "name": "FR-PAR-01"}]}}
    message = NetBoxAgent._pending_message(pending, outputs, "fr")
    assert "Création: vlans 'DMZ'" in message
    assert "api_key" not in message


def test_missing_prerequisite_forces_create_or_clarify_recovery():
    class MissingTools(FakeTools):
        def execute(self, name, arguments):
            if name == "netbox_read":
                return ToolResult(ok=False, message="Le préfixe 192.168.10.0/24 n’existe pas dans NetBox.")
            return super().execute(name, arguments)

    read = tool_call("netbox_read", {
        "app": "ipam", "endpoint": "prefixes", "method": "filter", "kwargs": {"prefix": "192.168.10.0/24"}
    }, "read-prefix")
    create = tool_call("netbox_write", {
        "app": "ipam", "endpoint": "prefixes", "action": "create", "data": {"prefix": "192.168.10.0/24", "site": 2}
    }, "create-prefix")
    client = FakeClient([
        Message(tool_calls=[read]),
        Message("Le préfixe n’existe pas."),
        Message(tool_calls=[create]),
        Message("Plan complet"),
        Message("Plan final"),
    ])
    result = NetBoxAgent(settings(), tools=MissingTools(), client=client).run("Affecte une IP dans 192.168.10.0/24")
    assert len(result.pending_confirmation) == 1
    assert "Création: prefixes '192.168.10.0/24'" in result.message
    assert "ipam/prefixes" not in result.message


def test_confirmation_matches_business_report_for_composed_request():
    pending = [
        PendingToolCall(id="site", name="netbox_write", arguments={
            "app": "dcim", "endpoint": "sites", "action": "create",
            "data": {"name": "Site-Test", "slug": "site-test", "status": "active"},
        }),
        PendingToolCall(id="server", name="netbox_write", arguments={
            "app": "dcim", "endpoint": "devices", "action": "create",
            "data": {"name": "srv-app-01", "site": "${site.data.id}", "status": "active"},
        }),
        PendingToolCall(id="vlan", name="netbox_write", arguments={
            "app": "ipam", "endpoint": "vlans", "action": "create",
            "data": {"vid": 500, "name": "SERVICES", "site": "${site.data.id}", "status": "active"},
        }),
        PendingToolCall(id="prefix", name="netbox_write", arguments={
            "app": "ipam", "endpoint": "prefixes", "action": "create",
            "data": {"prefix": "10.50.0.0/24", "site": "${site.data.id}", "vlan": "${vlan.data.id}", "is_pool": False},
        }),
        PendingToolCall(id="ip", name="netbox_write", arguments={
            "app": "ipam", "endpoint": "ip-addresses", "action": "create",
            "data": {"address": "${available-ip.data.0.address}", "device": "${server.data.id}"},
        }),
    ]
    message = NetBoxAgent._pending_message(pending, {}, "fr")
    for expected in ("Création: sites 'Site-Test'", "Création: devices 'srv-app-01'", "Création: vlans 'SERVICES'", "Création: prefixes '10.50.0.0/24'", "Création: ip addresses '${available-ip.data.0.address}'"):
        assert expected in message
    assert message.endswith("Confirmez-vous l’exécution de ces opérations ?")


def test_confirmation_matches_the_user_language():
    pending = [PendingToolCall(
        id="create-site",
        name="netbox_write",
        arguments={"app": "dcim", "endpoint": "sites", "action": "create", "data": {"name": "HomeLab", "slug": "homelab"}},
    )]
    english = NetBoxAgent._pending_message(pending, {}, "en")
    french = NetBoxAgent._pending_message(pending, {}, "fr")
    assert english.startswith("Pending changes awaiting your validation")
    assert "Create: sites 'HomeLab'" in english
    assert "Changes awaiting" not in french
    assert "Création: sites 'HomeLab'" in french
    assert NetBoxAgent._detect_language("Create a site named HomeLab") == "en"
    assert NetBoxAgent._detect_language("Create VLAN 493 named Test at site FR01 - Le Fief-Sauvin") == "en"
    assert NetBoxAgent._detect_language("Crée un site nommé HomeLab") == "fr"
    assert NetBoxAgent._detect_language("LAB-01") == "fr"


def test_confirmation_uses_generic_endpoint_labels_and_clean_deletes():
    pending = [
        PendingToolCall(id="manufacturer", name="netbox_write", arguments={
            "app": "dcim", "endpoint": "manufacturers", "action": "create", "data": {"name": "Juniper"},
        }),
        PendingToolCall(id="type", name="netbox_write", arguments={
            "app": "dcim", "endpoint": "device-types", "action": "create", "data": {"model": "EX4300"},
        }),
        PendingToolCall(id="ip-delete", name="netbox_write", arguments={
            "app": "ipam", "endpoint": "ip-addresses", "action": "delete", "data": {"id": 7, "address": "10.0.0.1/24"},
        }),
    ]
    message = NetBoxAgent._pending_message(pending, {}, "fr")
    assert "Création: manufacturers 'Juniper'" in message
    assert "Création: device types 'EX4300'" in message
    assert "Suppression: ip addresses '10.0.0.1/24'" in message
    assert "Attribution d’IP" not in message
    assert "nouvel objet" not in message
    assert "élément vérifié" not in message


def test_write_guard_is_endpoint_agnostic_after_live_read():
    args = {
        "app": "dcim", "endpoint": "interfaces", "action": "create",
        "data": {"name": "l3-10.50.0.1", "type": "virtual", "device": 1},
    }
    assert NetBoxAgent._write_guard(args, {("dcim", "interfaces")}, {1}, "fr") is None


def test_create_guard_blocks_an_existing_object_after_live_read():
    args = {"app": "dcim", "endpoint": "sites", "action": "create", "data": {"name": "LAB-PARIS-01"}}
    result = NetBoxAgent._write_guard(
        args,
        {("dcim", "sites")},
        {10},
        "fr",
        {("dcim", "sites"): [{"id": 10, "name": "LAB-PARIS-01"}]},
    )
    assert result is not None
    assert result.data["existing_object"] is True
    assert "aucune création supplémentaire" in result.message


def test_write_guard_requires_endpoint_scoped_live_update_or_delete_target():
    update = {"app": "dcim", "endpoint": "devices", "action": "update", "data": {"id": 42, "name": "srv"}}
    target = {("dcim", "devices")}
    assert NetBoxAgent._write_guard(update, set(), set(), "en") is not None
    assert NetBoxAgent._write_guard(update, target, {42}, "en", {("dcim", "interfaces"): [{"id": 42}]}) is not None
    assert NetBoxAgent._write_guard(update, target, {42}, "en", {("dcim", "devices"): [{"id": 42}]}) is None

    missing = {"app": "dcim", "endpoint": "devices", "action": "delete", "data": {}}
    assert NetBoxAgent._write_guard(missing, target, set(), "en", {("dcim", "devices"): []}) is not None


def test_available_ips_uses_native_prefix_endpoint():
    class Available:
        def list(self, limit):
            return [{"address": "10.30.0.1/24"}, {"address": "10.30.0.2/24"}]

    class Prefix:
        available_ips = Available()
        def __str__(self):
            return "10.30.0.0/24"

    class Prefixes:
        def get(self, *args, **kwargs):
            return Prefix()

    tools = object.__new__(NetBoxTools)
    tools.api = SimpleNamespace(ipam=SimpleNamespace(prefixes=Prefixes()))
    result = tools._read_available_ips(NetBoxReadArgs(
        app="ipam", endpoint="available_ips", kwargs={"prefix": "10.30.0.0/24"}, limit=1
    ))
    assert result.ok is True
    assert result.data == [{"address": "10.30.0.1/24"}]


def test_recent_context_is_injected_into_agent_messages():
    client = FakeClient([
        Message("Je rattache les interfaces déjà citées."),
        Message("Les interfaces citées doivent être vérifiées avant toute action."),
    ])
    history = [
        {"role": "assistant", "text": "Il reste à rattacher 1/1/21 à 1/1/24 au LAG po1."},
    ]
    NetBoxAgent(settings(), tools=FakeTools(), client=client).run("Quel est le rattachement des interfaces ?", history=history)
    sent = client.calls[0]["messages"]
    assert any("1/1/21 à 1/1/24" in item.get("content", "") for item in sent)


def test_symbolic_reference_resolution_preserves_integer_id():
    outputs = {"create-lag": {"data": {"id": 64}}}
    resolved = NetBoxAgent._resolve_references(
        {"data": {"lag": "${create-lag.data.id}"}}, outputs
    )
    assert resolved["data"]["lag"] == 64


def test_read_reference_is_materialized_before_confirmation():
    outputs = {"read-lag": {"data": {"id": 65}}}
    resolved = NetBoxAgent._resolve_available_references(
        {"data": {"lag": "${read-lag.data.id}"}}, outputs
    )
    assert resolved["data"]["lag"] == 65


def test_universal_write_create_update_delete(monkeypatch):
    class Record:
        def __init__(self, object_id, data=None):
            self.id = object_id
            self.data = {"id": object_id, **(data or {})}
            self.deleted = False

        def serialize(self):
            return dict(self.data)

        def update(self, data):
            self.data.update(data)
            return True

        def delete(self):
            self.deleted = True
            return True

    class Endpoint:
        def __init__(self):
            self.records = {7: Record(7, {"name": "existing"})}

        def create(self, data):
            record = Record(8, data)
            self.records[8] = record
            return record

        def get(self, object_id):
            return self.records.get(int(object_id))

    endpoint = Endpoint()
    tools = object.__new__(NetBoxTools)
    monkeypatch.setattr(tools, "_resolve_endpoint", lambda app, resource: (endpoint, app, None, resource))

    created = tools.netbox_write(NetBoxWriteArgs(app="dcim", endpoint="devices", action="create", data={"name": "new"}))
    updated = tools.netbox_write(NetBoxWriteArgs(app="dcim", endpoint="devices", action="update", data={"id": 7, "name": "renamed"}))
    deleted = tools.netbox_write(NetBoxWriteArgs(app="dcim", endpoint="devices", action="delete", data={"id": 7}))

    assert created.data["id"] == 8
    assert updated.data["name"] == "renamed"
    assert deleted.data == {"id": 7, "deleted": True}
    assert endpoint.records[7].deleted is True


def test_read_tool_result_is_returned_to_llm():
    read = tool_call("netbox_read", {
        "app": "dcim", "endpoint": "devices", "method": "filter", "kwargs": {"name": "sw-01"}
    })
    client = FakeClient([Message(tool_calls=[read]), Message("SW-01 existe.")])
    result = NetBoxAgent(settings(), tools=FakeTools(), client=client).run("Cherche SW-01")
    assert result.message == "SW-01 existe."
    assert result.tool_results[0].ok is True



