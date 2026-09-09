from __future__ import annotations

import json
import uuid
from typing import Any

import httpx


class MCPGateway:
    """Small MCP JSON-RPC gateway with one session per gateway instance."""

    def __init__(self, url: str, bearer: str, timeout: float = 60.0, http: httpx.Client | None = None):
        self.url = url.rstrip("/")
        self.headers = {
            "Authorization": f"Bearer {bearer}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        self.http = http or httpx.Client(timeout=timeout)
        self.session_id: str | None = None

    def _rpc(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        request = {"jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": method, "params": params or {}}
        headers = dict(self.headers)
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        response = self.http.post(self.url, headers=headers, json=request)
        response.raise_for_status()
        self.session_id = response.headers.get("mcp-session-id", self.session_id)
        text = response.text
        if text.startswith("event:"):
            text = text.split("data:", 1)[1].strip()
        payload = json.loads(text)
        if "error" in payload:
            raise RuntimeError(payload["error"].get("message", "MCP error"))
        return payload["result"]

    def initialize(self) -> None:
        self._rpc("initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "netwaive-copilot", "version": "0.1.0"},
        })

    def tools(self) -> list[dict[str, Any]]:
        if not self.session_id:
            self.initialize()
        return list(self._rpc("tools/list").get("tools", []))

    def call(self, name: str, arguments: dict[str, Any]) -> Any:
        if not self.session_id:
            self.initialize()
        result = self._rpc("tools/call", {"name": name, "arguments": arguments})
        return result.get("content", result)
