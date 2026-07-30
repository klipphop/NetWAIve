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
    llm_timeout: float = 60.0
    max_agent_turns: int = Field(default=8, ge=1, le=20)
    max_search_results: int = Field(default=20, ge=1, le=100)
