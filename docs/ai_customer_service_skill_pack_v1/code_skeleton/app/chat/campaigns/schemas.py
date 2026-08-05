from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class FrequencyCap(BaseModel):
    max_impressions_per_customer: int = 1
    cooldown_hours: int = 24


class CampaignDefinition(BaseModel):
    campaign_id: str
    version: int
    enabled: bool = False
    title: str
    valid_from: datetime
    valid_to: datetime
    eligible_journey_stages: list[str] = Field(default_factory=list)
    eligible_product_ids: list[str] = Field(default_factory=list)
    benefit: dict[str, Any]
    frequency_cap: FrequencyCap
    suppression_conditions: list[str] = Field(default_factory=list)
    required_disclosures: list[str] = Field(default_factory=list)


class CampaignEligibilityResult(BaseModel):
    eligible: bool
    reason_codes: list[str] = Field(default_factory=list)
