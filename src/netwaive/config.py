from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration chargée depuis l'environnement ou un fichier .env."""

    model_config = SettingsConfigDict(env_prefix="NETBOX_LLM_", env_file=".env", extra="ignore")

    netbox_url: str = Field(description="URL racine NetBox, sans /api")
    netbox_token: SecretStr
    netbox_verify_ssl: bool = True
    llm_base_url: str
    llm_api_key: SecretStr
    llm_model: str
    mcp_server_url: str = "http://127.0.0.1:8002/mcp"
    mcp_auth_token: SecretStr = SecretStr("")
    llm_timeout: float = 60.0
    max_agent_turns: int = Field(default=8, ge=1, le=20)
    max_search_results: int = Field(default=20, ge=1, le=100)


def validate(config: dict, release: str) -> None:
    """NetBox plugin configuration hook (called by NetBox at startup)."""
    if not isinstance(config, dict):
        raise TypeError("netwaive PLUGINS_CONFIG must be a mapping")
    if config.get("mcp_server_url") and not str(config["mcp_server_url"]).startswith(("http://", "https://")):
        raise ValueError("mcp_server_url must be an http(s) URL")
