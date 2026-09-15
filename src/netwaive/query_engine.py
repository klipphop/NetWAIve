from __future__ import annotations

import json
from typing import Any
from .mcp_client import MCPClient
from .query_models import QueryResult, Evidence


class QueryEngine:
    """Deterministic MCP query boundary; the LLM formats, Python computes."""
    def __init__(self, mcp: MCPClient):
        self.mcp = mcp

    def compare(self, left_endpoint: str, left_params: dict[str, Any], right_endpoint: str, scope_field: str) -> QueryResult:
        raw = self.mcp.call("netbox_compare_scoped_relations", {"left_endpoint": left_endpoint, "left_params": left_params, "right_endpoint": right_endpoint, "right_scope_field": scope_field, "include_inherited": True})
        items = list(raw.get("missing", []))
        result = QueryResult(kind="comparison", title="Comparaison NetBox", items=items, total=len(items), relation={"left_count": raw.get("left_count"), "right_count": raw.get("right_count"), "covered_count": raw.get("covered_count"), "missing_count": raw.get("missing_count"), "scope_field": raw.get("relation_field")}, evidence=[Evidence(tool="netbox_compare_scoped_relations", endpoint=left_endpoint, filters=left_params, pages=0, object_count=raw.get("left_count", 0))])
        return result.assert_consistent()
