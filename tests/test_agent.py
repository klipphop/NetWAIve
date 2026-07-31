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
    ])
    result = NetBoxAgent(settings(), tools=FakeTools(), client=client).run(
        "ajouter un subnet 10.50.0.0/24 et l’affecter au vlan 500"
    )
    assert len(client.calls) == 5
    assert len(result.pending_confirmation) == 1
    assert "Création du préfixe" in result.message
    assert "Compris" not in result.message


def test_universal_write_requires_confirmation():
    call = tool_call("netbox_write", {
        "app": "dcim", "endpoint": "devices", "action": "create", "data": {"name": "sw-02"}
    })
    read = tool_call("netbox_read", {
        "app": "dcim", "endpoint": "devices", "method": "filter", "kwargs": {"name": "sw-02"}
    }, "read-device")
    agent = NetBoxAgent(settings(), tools=FakeTools(), client=FakeClient([
        Message(tool_calls=[read]), Message(tool_calls=[call]), Message("Plan complet")
    ]))
    result = agent.run("Crée sw-02")
    assert result.pending_confirmation[0].name == "netbox_write"
    assert "Création de l’équipement : 'sw-02'" in result.message
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
    ])
    result = NetBoxAgent(settings(), tools=FakeTools(), client=client).run("Crée po1 et attache les quatre interfaces")
    assert len(result.pending_confirmation) == 5
    assert result.message.startswith("Modifications en attente de votre validation")
    assert "Création du LAG : 'po1'" in result.message
    assert result.message.count("Rattachement de l’interface") == 4
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
    assert "Création du VLAN : VID 250 — Nom 'DMZ'" in message
    assert "Rattaché au site FR-PAR-01" in message
    assert "ipam/vlans" not in message
    assert "api_key" not in message
    assert "site=" not in message
    assert "id:" not in message


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
    ])
    result = NetBoxAgent(settings(), tools=MissingTools(), client=client).run("Affecte une IP dans 192.168.10.0/24")
    assert len(result.pending_confirmation) == 1
    assert "Création du préfixe : 192.168.10.0/24" in result.message
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
    assert "• Création du site : 'Site-Test'" in message
    assert "• Création du serveur : 'srv-app-01' (Rattaché au site Site-Test)" in message
    assert "• Création du VLAN : VID 500 — Nom 'SERVICES' (Rattaché au site Site-Test)" in message
    assert "• Création du préfixe : 10.50.0.0/24 (Rattaché au VLAN SERVICES et au site Site-Test)" in message
    assert "• Attribution d’IP : La première IP disponible dans 10.50.0.0/24 sera attribuée à srv-app-01 dès validation." in message
    for forbidden in ("dcim/", "ipam/", "${", "is_pool", "slug=", "status="):
        assert forbidden not in message
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
    assert "Create site: 'HomeLab'" in english
    assert "Changes awaiting" not in french
    assert "Création du site : 'HomeLab'" in french
    assert NetBoxAgent._detect_language("Create a site named HomeLab") == "en"
    assert NetBoxAgent._detect_language("Create VLAN 493 named Test at site FR01 - Le Fief-Sauvin") == "en"
    assert NetBoxAgent._detect_language("Crée un site nommé HomeLab") == "fr"
    assert NetBoxAgent._detect_language("LAB-01") == "fr"


def test_confirmation_uses_precise_netbox_resource_labels_and_clean_deletes():
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
    assert "Création : Fabricant 'Juniper'" in message
    assert "Création : Type d’équipement 'EX4300'" in message
    assert "Suppression de l’adresse IP : '10.0.0.1/24'" in message
    assert "Attribution d’IP" not in message
    assert "nouvel objet" not in message
    assert "élément vérifié" not in message


def test_l3_interface_name_cannot_be_derived_from_an_ip():
    args = {
        "app": "dcim", "endpoint": "interfaces", "action": "create",
        "data": {"name": "l3-10.50.0.1", "type": "virtual", "device": 1},
    }
    result = NetBoxAgent._write_guard(args, {("dcim", "interfaces")}, {1}, "fr")
    assert result is not None
    assert result.data["invalid_l3_interface_name"] is True
    assert "Vlan71" in result.message


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
    NetBoxAgent(settings(), tools=FakeTools(), client=client).run("attache les interfaces", history=history)
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
