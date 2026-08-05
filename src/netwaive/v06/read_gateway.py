from __future__ import annotations

from typing import Any

from ..models import NetBoxReadArgs
from ..tools import NetBoxTools


class PynetboxReadOnlyGateway:
    """Thin read-only adapter; write methods are intentionally not exposed."""

    def __init__(self, tools: NetBoxTools):
        self._tools = tools

    def read(self, app: str, endpoint: str, *, filters: dict[str, Any]) -> list[dict[str, Any]]:
        result = self._tools.netbox_read(NetBoxReadArgs(app=app, endpoint=endpoint, method="filter", kwargs=filters, limit=200))
        if not result.ok:
            raise RuntimeError(result.message)
        if isinstance(result.data, list):
            return [item for item in result.data if isinstance(item, dict)]
        if isinstance(result.data, dict):
            return [result.data]
        return []
