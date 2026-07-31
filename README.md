# NetWAIve

Package Python autonome fournissant un agent LLM à double casquette : expert IT/réseau et opérateur NetBox RO/RW. Toutes les opérations NetBox utilisent exclusivement `pynetbox`.

## Arborescence

```text
netwaive/
├── pyproject.toml
├── setup.py
├── .env.example
├── README.md
├── src/netwaive/
│   ├── __init__.py
│   ├── __main__.py
│   ├── agent.py
│   ├── cli.py
│   ├── config.py
│   ├── errors.py
│   ├── models.py
│   ├── plugin.py
│   ├── prompt.py
│   ├── template_content.py
│   ├── urls.py
│   ├── views.py
│   ├── templates/netwaive/
│   │   ├── chat.html
│   │   └── floating_widget.html
│   ├── static/netwaive/
│   │   ├── chat.js
│   │   ├── floating.css
│   │   └── floating.js
│   └── tools.py
└── tests/
    └── test_agent.py
```

## Installation production

```bash
cd /chemin/netwaive
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
pip install .
```

Installation développement :

```bash
pip install -e '.[dev]'
pytest
```

Construction d'un wheel :

```bash
python -m build
pip install dist/netwaive-0.4.0-py3-none-any.whl
```

## Configuration

```bash
cp .env.example .env
chmod 600 .env
```

Variables principales :

```text
NETBOX_LLM_NETBOX_URL=https://netbox.example.org
NETBOX_LLM_NETBOX_TOKEN=...
NETBOX_LLM_LLM_BASE_URL=https://api.openai.com/v1
NETBOX_LLM_LLM_API_KEY=...
NETBOX_LLM_LLM_MODEL=gpt-4.1-mini
```

Le token NetBox doit appliquer le moindre privilège. Utiliser un token RO pour les consultations et un token RW limité pour les mutations.

## Chargement comme plugin NetBox

Installer le wheel dans le venv NetBox puis ajouter le package à `configuration.py` :

```python
import os

PLUGINS = [
    "netwaive",
]

PLUGINS_CONFIG = {
    "netwaive": {
        "write_enabled": False,
        "netbox_url": "https://netbox.example.org",
        "netbox_token": os.environ["NETBOX_LLM_NETBOX_TOKEN"],
        "netbox_verify_ssl": True,
        "llm_base_url": "https://api.openai.com/v1",
        "llm_api_key": os.environ["NETBOX_LLM_LLM_API_KEY"],
        "llm_model": "gpt-4.1-mini",
    },
}
```

```bash
/opt/netbox/venv/bin/pip install dist/netwaive-0.4.0-py3-none-any.whl
/opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py check
systemctl restart netbox netbox-rq
```

La version `0.4.0` inclut le widget global flottant/docké et une page de chat dédiée. Le widget est injecté via `PluginTemplateExtension` lorsque le plugin est activé dans `PLUGINS`.

## Utilisation Python

```python
from netwaive import NetBoxAgent, Settings

agent = NetBoxAgent(Settings())

# Conseil théorique : aucun appel NetBox n'est nécessaire.
answer = agent.run("Explique la différence entre eBGP et iBGP")
print(answer.message)

# Lecture NetBox.
answer = agent.run("Quels devices correspondent à sw-core ?")
print(answer.message)

# Première passe : aucune écriture, retour d'une confirmation.
preview = agent.run("Crée le device sw-02 au site paris avec le rôle switch et le modèle 9200L")
print(preview.message)

# Après confirmation explicite de l'application appelante.
result = agent.run(
    "Crée le device sw-02 au site paris avec le rôle switch et le modèle 9200L",
    confirm_write=True,
)
print(result.message)
```

## CLI

```bash
netwaive "Explique le route-reflector BGP"
netwaive "Recherche les devices core"
netwaive --confirm-write "Crée le device sw-02 ..."
```

## Tools universels exposés au LLM

- `netbox_read(app, endpoint, method="filter", kwargs={}, limit=50)` : lecture dynamique de toute app et tout endpoint.
- `netbox_write(app, endpoint, action, data)` : création, mise à jour ou suppression universelle.
- `get_endpoint_schema(app, endpoint)` : découverte OpenAPI live des méthodes, filtres et champs.

Exemples :

```text
netbox_read(app="ipam", endpoint="vlans", kwargs={"site": "fr01"})
netbox_read(app="dcim", endpoint="cables", kwargs={"device": "sw-01"})
get_endpoint_schema(app="dcim", endpoint="interfaces")
netbox_read(app="plugins", endpoint="plugin_slug/endpoint_slug", kwargs={})
```

Les outils RW sont bloqués tant qu'ils ne sont pas confirmés. Après une écriture confirmée, son résultat réel est réinjecté dans la boucle afin que l'agent puisse poursuivre un workflow multi-étapes avec les IDs retournés par NetBox.
