try:
    from netbox.plugins import PluginConfig
except ImportError:  # Tests hors environnement NetBox.
    class PluginConfig:
        django_apps: list[str] = []
        min_version = None
        max_version = None

        @classmethod
        def validate(cls, config: dict, release: str) -> None:
            return None

from .config import Settings
from .models import AgentResponse, PendingToolCall, ToolResult


class NetWAIveConfig(PluginConfig):
    name = "netwaive"
    verbose_name = "NetBox Assistant"
    description = "Lightweight NetBox MCP client with RW approval gate."
    version = "0.1.4"
    base_url = "netwaive"
    min_version = "4.4.0"


config = NetWAIveConfig
__version__ = "0.1.4"
__all__ = ["AgentResponse", "PendingToolCall", "Settings", "ToolResult", "NetWAIveConfig", "config"]
