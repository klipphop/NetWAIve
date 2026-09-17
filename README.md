# NetWAIve

NetWAIve is a NetBox plugin providing a server-side, MCP-first infrastructure assistant. The plugin keeps credentials on the server, performs read-only inspection before mutations, creates one typed French Change Plan, requires explicit approval, executes one controlled batch, and verifies the result by read-back.

## Requirements

- NetBox 4.4+ (tested on NetBox 4.6)
- Python 3.11+
- An MCP Streamable HTTP server exposing the NetWAIve tool contract
- An OpenAI-compatible LLM endpoint with tool calling (Google AI Studio Gemini, Vertex AI OpenAI-compatible endpoint, OpenAI-compatible self-hosted providers, etc.)

The LLM provider is configurable. The deterministic safety boundary is Python/MCP, not the model prompt.

## Build and install

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
pytest
python -m build --wheel
```

Install the generated wheel into the NetBox virtualenv:

```bash
/opt/netbox/venv/bin/pip install dist/netwaive-<version>-py3-none-any.whl
/opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py check
/opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py collectstatic --no-input
systemctl restart netbox
```

Use the actual paths and service names of the target installation; `/opt/netbox` is only an example.

## NetBox configuration

Install the wheel into the NetBox virtualenv, then configure `PLUGINS` and `PLUGINS_CONFIG` in `configuration.py`. Keep all secrets in an environment file or secret manager; do not commit them or expose them to the browser.

```python
PLUGINS = ["netwaive"]

PLUGINS_CONFIG = {
    "netwaive": {
        "netbox_url": "https://netbox.example.org",
        "netbox_token": os.environ["NETWAIve_NETBOX_TOKEN"],
        "netbox_verify_ssl": True,
        "llm_base_url": os.environ["NETWAIve_LLM_BASE_URL"],
        "llm_api_key": os.environ["NETWAIve_LLM_API_KEY"],
        "llm_model": os.environ["NETWAIve_LLM_MODEL"],
        "mcp_server_url": os.environ["NETWAIve_MCP_URL"],
        "mcp_auth_token": os.environ["NETWAIve_MCP_TOKEN"],
        "llm_timeout": 120.0,
        "max_agent_turns": 8,
    },
}
```

The MCP bearer and NetBox API token are separate credentials. Use least-privilege tokens and keep the MCP endpoint loopback-only unless external access is explicitly required.

## Google AI Studio

Gemini's OpenAI-compatible endpoint can be configured without changing the plugin:

```text
NETWAIve_LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
NETWAIve_LLM_MODEL=<Gemini model supporting function calling>
NETWAIve_LLM_API_KEY=<server-side Google AI Studio key>
```

Validate the selected model's tool-calling behavior before enabling production writes.

## Runtime behavior

```text
LLM intent
  -> read-only MCP inspection
  -> deterministic selection/exclusion/duplicate resolution
  -> schema and required-field preflight
  -> dependency validation and MCP dry-run
  -> visual Plan de changement
  -> explicit approval
  -> netbox_batch_execute
  -> field-level read-back verification
```

Required business custom fields are never invented. If the live NetBox schema requires a value without an authorized deterministic source, NetWAIve asks a structured question and creates no pending write.

## Deployment checklist

```bash
python -m pytest -q
python -m compileall -q src
python -m build --wheel
# install wheel into the target NetBox virtualenv
python /path/to/netbox/netbox/manage.py check
python /path/to/netbox/netbox/manage.py makemigrations --check --dry-run
python /path/to/netbox/netbox/manage.py collectstatic --no-input
# restart target services
# verify authenticated NetBox page, MCP initialize/tools/list, and a read-only query
```

Also verify that source and served static asset hashes match after `collectstatic`. Run a browser E2E test before enabling writes in a new environment.

## Security

- Credentials remain server-side.
- Read-only tools bypass the write Gatekeeper.
- Mutations require one server-stored Change Plan and explicit approval.
- Selection, dependencies, schemas, custom fields, and read-back are validated in Python.
- User feedback is review telemetry only; it is never injected into LLM instructions.
- Do not reuse production secrets in development or documentation.
