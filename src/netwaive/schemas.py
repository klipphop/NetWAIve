from __future__ import annotations

from typing import Any
from pydantic import BaseModel, ConfigDict, Field
from .contracts import ChangePlan


class QuestionOption(BaseModel):
    value: str
    label: str


class UserQuestion(BaseModel):
    kind: str = Field(pattern="^(boolean|choice|text)$")
    prompt: str
    options: list[QuestionOption] = Field(default_factory=list)
    required: bool = True


class ToolResult(BaseModel):
    ok: bool
    message: str
    data: Any = None


class AgentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str
    change_plan: ChangePlan | None = None
    tool_results: list[ToolResult] = Field(default_factory=list)
    quick_replies: list[str] = Field(default_factory=list)
    question: UserQuestion | None = None
