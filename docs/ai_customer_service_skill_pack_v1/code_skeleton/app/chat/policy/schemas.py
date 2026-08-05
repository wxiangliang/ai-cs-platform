from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class BusinessAction(StrEnum):
    NO_PROACTIVE_ACTION = "NO_PROACTIVE_ACTION"
    START_ONBOARDING = "START_ONBOARDING"
    START_PRODUCT_DISCOVERY = "START_PRODUCT_DISCOVERY"
    OFFER_PRODUCT_COMPARE = "OFFER_PRODUCT_COMPARE"
    OFFER_PURCHASE_HELP = "OFFER_PURCHASE_HELP"
    MENTION_CAMPAIGN = "MENTION_CAMPAIGN"
    RESUME_PLAYBOOK_HINT = "RESUME_PLAYBOOK_HINT"


class ActionCandidate(BaseModel):
    action: BusinessAction
    priority: int
    confidence: float = 1.0
    optional: bool = True
    payload: dict[str, Any] = Field(default_factory=dict)
    reason_codes: list[str] = Field(default_factory=list)


class SuppressionResult(BaseModel):
    suppressed: bool
    reason_codes: list[str] = Field(default_factory=list)


class NextBestActionResult(BaseModel):
    selected: ActionCandidate
    candidates: list[ActionCandidate] = Field(default_factory=list)
    suppressed_candidates: list[ActionCandidate] = Field(default_factory=list)
    suppression_reason_codes: list[str] = Field(default_factory=list)
    policy_version: str = "rules-v1"
