"""NetWAIve."""
from .agent import NetBoxAgent, AgentResponse
from .config import Settings
from .tools import NetBoxTools

__all__ = ["AgentResponse", "NetBoxAgent", "NetBoxTools", "Settings"]
__version__ = "0.5.1.post8"

try:  # Chargé uniquement dans le venv NetBox.
    from netbox.plugins import PluginConfig

    class NetWAIveConfig(PluginConfig):
        name = "netwaive"
        verbose_name = "NetWAIve"
        description = "Assistant LLM NetBox utilisant exclusivement pynetbox"
        version = __version__
        author = "NetDevOps"
        base_url = "netwaive"
        min_version = "4.0.0"
        default_settings = {
            "write_enabled": False,
            "netbox_url": "",
            "netbox_token": "",
            "netbox_verify_ssl": True,
            "llm_base_url": "",
            "llm_api_key": "",
            "llm_model": "",
        }

    config = NetWAIveConfig
    __all__.append("NetWAIveConfig")
except ModuleNotFoundError:
    config = None
