from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class RecommendationRequest(BaseModel):
    category: str
    hard_constraints: dict[str, Any] = Field(default_factory=dict)
    preferences: dict[str, Any] = Field(default_factory=dict)
    max_items: int = 4


class ProductCandidate(BaseModel):
    product_id: str
    facts: dict[str, Any]
    commercial_weight: float = 0.0


class RankedProduct(BaseModel):
    product_id: str
    score: float
    score_breakdown: dict[str, float]
    reasons: list[str]
    tradeoffs: list[str]
    fact_snapshot: dict[str, Any]


class RecommendationResult(BaseModel):
    items: list[RankedProduct]
    recalled_count: int
    filtered_count: int
    filter_reasons: dict[str, list[str]]
    reason_codes: list[str] = Field(default_factory=list)
