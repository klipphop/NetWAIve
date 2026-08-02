import json
from types import SimpleNamespace

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


def test_netbox_errors_preserve_native_payload():
    message = NetBoxTools._friendly_error("Related object not found: device role 'Switch'")
    assert message == "Related object not found: device role 'Switch'"


def test_symbolic_reference_resolves_call_aliases_and_result_ids():
    outputs = {"toolu_abc": {"ok": True, "data": {"id": 12}}}
    assert NetBoxAgent._resolve_reference("toolu_abc.data.id", outputs) == 12
    assert NetBoxAgent._resolve_reference("toolu_abc.id", outputs) == 12
    assert NetBoxAgent._resolve_reference("call_o1hFJh1F074DRdjM1l4v8ACm.data.id", outputs) == 12


def test_parent_dependencies_are_ordered_before_device_creation():
    manufacturer = PendingToolCall(id="manufacturer", name="netbox_write", arguments={"app":"dcim","endpoint":"manufacturers","action":"create","data":{"name":"Acme"}})
    device_type = PendingToolCall(id="type", name="netbox_write", arguments={"app":"dcim","endpoint":"device-types","action":"create","data":{"model":"X1","manufacturer":"${manufacturer.data.id}"}})
    device = PendingToolCall(id="device", name="netbox_write", arguments={"app":"dcim","endpoint":"devices","action":"create","data":{"name":"sw-01","device_type":"${type.data.id}"}})
    ordered = NetBoxAgent._order_pending([device, device_type, manufacturer])
    assert [call.id for call in ordered] == ["manufacturer", "type", "device"]


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


def test_dtl_read_loads_official_template_without_netbox_mutation(monkeypatch):
    class Response:
        status_code = 200
        ok = True
        text = "manufacturer: Cisco\nmodel: Catalyst 9300-48P\ninterfaces:\n  - name: GigabitEthernet1/0/1\n"
        def raise_for_status(self): pass
        def json(self): return {"default_branch": "master"}
    monkeypatch.setattr("netwaive.tools.requests.get", lambda url, timeout: Response())
    result = NetBoxTools(settings()).netbox_read(NetBoxReadArgs(app="dtl", endpoint="device-types", method="get", kwargs={"manufacturer":"Cisco", "model":"C9300-48P"}))
    assert result.ok and result.data["device_type"]["model"] == "Catalyst 9300-48P"
    assert result.data["component_templates"]["interfaces"][0]["name"] == "GigabitEthernet1/0/1"
    assert "/master/device-types/" in result.data["source"]
    plan = result.data["import_plan"]
    assert plan[1]["arguments"]["data"]["slug"] == "c9300-48p"
    assert plan[1]["arguments"]["data"]["u_height"] == 1
    assert plan[2]["arguments"]["endpoint"] == "interface-templates"


def test_dtl_directory_search_returns_c9200_candidates(monkeypatch):
    class Response:
        def __init__(self, status_code, payload=None): self.status_code, self.payload, self.ok = status_code, payload or [], status_code < 400
        def raise_for_status(self): pass
        def json(self): return self.payload
    replies = iter([Response(200, {"default_branch":"master"}), Response(404), Response(200, [{"type":"file","name":"C9200-24T.yaml"}, {"type":"file","name":"C9200-48P.yaml"}])])
    monkeypatch.setattr("netwaive.tools.requests.get", lambda url, timeout: next(replies))
    result = NetBoxTools(settings()).netbox_read(NetBoxReadArgs(app="dtl", endpoint="device-types", method="get", kwargs={"manufacturer":"Cisco", "model":"Catalyst 9200"}))
    assert result.ok and result.data["candidates"] == ["C9200-24T", "C9200-48P"]


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


def test_write_guard_requires_live_read_and_observed_update_id():
    args = {"app": "dcim", "endpoint": "devices", "action": "update", "data": {"id": 42, "name": "srv"}}
    assert NetBoxAgent._write_guard(args, set(), set(), "en") is not None
    assert NetBoxAgent._write_guard(args, {("dcim", "devices")}, set(), "en") is not None
    assert NetBoxAgent._write_guard(args, {("dcim", "devices")}, {42}, "en") is None


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
