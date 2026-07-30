"""NetBox LLM Chat."""
from .agent import NetBoxAgent, AgentResponse
from .config import Settings
from .tools import NetBoxTools

__all__ = ["AgentResponse", "NetBoxAgent", "NetBoxTools", "Settings"]
__version__ = "0.3.7"

try:  # Chargé uniquement dans le venv NetBox.
    from netbox.plugins import PluginConfig

    class NetBoxLLMChatConfig(PluginConfig):
        name = "netbox_llm_chat"
        verbose_name = "NetBox LLM Chat"
        description = "Assistant LLM NetBox utilisant exclusivement pynetbox"
        version = __version__
        author = "NetDevOps"
        base_url = "netbox-llm-chat"
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

    config = NetBoxLLMChatConfig
    __all__.append("NetBoxLLMChatConfig")
except ModuleNotFoundError:
    config = None
