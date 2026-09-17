from __future__ import annotations

from typing import Any
from pydantic import BaseModel, ConfigDict, Field, field_validator


class SelectionItem(BaseModel):
    model_config = ConfigDict(extra="allow")
    key: str = Field(min_length=1)
    label: str = Field(min_length=1)
    object_id: int | None = Field(default=None, gt=0)
    endpoint: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SelectionExclusion(BaseModel):
    key: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class SelectionResult(BaseModel):
    """Canonical, auditable selection produced after live inspection."""
    model_config = ConfigDict(extra="forbid")
    candidates: list[SelectionItem] = Field(default_factory=list)
    selected: list[SelectionItem] = Field(default_factory=list)
    excluded: list[SelectionExclusion] = Field(default_factory=list)
    duplicates: list[SelectionItem] = Field(default_factory=list)

    @field_validator("candidates", "selected", "duplicates")
    @classmethod
    def unique_keys(cls, value: list[SelectionItem]) -> list[SelectionItem]:
        keys = [item.key for item in value]
        if len(keys) != len(set(keys)):
            raise ValueError("selection contains duplicate keys")
        return value

    def validate_consistency(self) -> "SelectionResult":
        candidate_keys = {item.key for item in self.candidates}
        selected_keys = {item.key for item in self.selected}
        excluded_keys = {item.key for item in self.excluded}
        duplicate_keys = {item.key for item in self.duplicates}
        if not selected_keys <= candidate_keys:
            raise ValueError("selected item is not a candidate")
        if not excluded_keys <= candidate_keys:
            raise ValueError("excluded item is not a candidate")
        if not duplicate_keys <= candidate_keys:
            raise ValueError("duplicate item is not a candidate")
        if selected_keys & excluded_keys:
            raise ValueError("item cannot be selected and excluded")
        if selected_keys & duplicate_keys:
            raise ValueError("existing duplicate cannot be selected for creation")
        return self


def selection_from_payload(payload: Any) -> SelectionResult | None:
    if payload is None:
        return None
    result = SelectionResult.model_validate(payload)
    return result.validate_consistency()
