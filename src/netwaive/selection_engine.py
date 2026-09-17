from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .selection import SelectionExclusion, SelectionItem, SelectionResult


class SelectionEngine:
    """Build auditable selections from live MCP collections.

    No resource names, endpoint names, IDs, or business rules are embedded here.
    Callers provide the collection and explicit exclusion decisions.
    """

    @staticmethod
    def _rows(payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, dict):
            rows = payload.get("results", payload.get("items", []))
        else:
            rows = payload
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise ValueError("selection source must be a list or paginated results collection")
        return rows

    @staticmethod
    def _label(row: dict[str, Any], label_fields: Iterable[str] = ()) -> str:
        for field in (*label_fields, "display", "name", "label", "slug", "prefix", "id"):
            value = row.get(field)
            if value not in (None, ""):
                return str(value)
        raise ValueError("selection source contains an unlabeled object")

    @staticmethod
    def _key(row: dict[str, Any], endpoint: str, key_field: str = "id") -> str:
        value = row.get(key_field)
        if value in (None, ""):
            value = row.get("url") or row.get("display") or row.get("name")
        if value in (None, ""):
            raise ValueError("selection source object has no stable key")
        return f"{endpoint.strip('/')}/{value}"

    def build(
        self,
        payload: Any,
        *,
        endpoint: str,
        key_field: str = "id",
        label_fields: Iterable[str] = (),
        exclude_keys: dict[str, str] | None = None,
        duplicate_keys: set[str] | None = None,
    ) -> SelectionResult:
        excluded = exclude_keys or {}
        duplicates = duplicate_keys or set()
        candidates: list[SelectionItem] = []
        selected: list[SelectionItem] = []
        excluded_items: list[SelectionExclusion] = []
        duplicate_items: list[SelectionItem] = []
        seen: set[str] = set()
        for row in self._rows(payload):
            key = self._key(row, endpoint, key_field)
            item = SelectionItem(
                key=key,
                label=self._label(row, label_fields),
                object_id=row.get("id") if isinstance(row.get("id"), int) else None,
                endpoint=endpoint.strip("/"),
                metadata=dict(row),
            )
            if key in seen:
                continue
            seen.add(key)
            candidates.append(item)
            if key in duplicates:
                duplicate_items.append(item)
            elif key in excluded:
                excluded_items.append(SelectionExclusion(key=key, reason=excluded[key]))
            else:
                selected.append(item)
        result = SelectionResult(candidates=candidates, selected=selected, excluded=excluded_items, duplicates=duplicate_items)
        return result.validate_consistency()
