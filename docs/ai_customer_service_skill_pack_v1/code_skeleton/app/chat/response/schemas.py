from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ResponsePart(BaseModel):
    type: str
    priority: int
    required: bool
    payload: dict[str, Any] = Field(default_factory=dict)


class ResponsePlan(BaseModel):
    parts: list[ResponsePart]
    dropped_parts: list[ResponsePart] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
