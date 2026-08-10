from __future__ import annotations

from openai import OpenAI

from .config import Settings
from .gatekeeper import GatekeeperAgent
from .mcp_client import MCPClient


def build_agent(settings: Settings) -> GatekeeperAgent:
    mcp = MCPClient(settings.mcp_server_url, settings.mcp_auth_token.get_secret_value(), settings.llm_timeout)
    llm = OpenAI(base_url=settings.llm_base_url, api_key=settings.llm_api_key.get_secret_value(), timeout=settings.llm_timeout)
    return GatekeeperAgent(llm, mcp, settings.llm_model, settings.max_agent_turns)
