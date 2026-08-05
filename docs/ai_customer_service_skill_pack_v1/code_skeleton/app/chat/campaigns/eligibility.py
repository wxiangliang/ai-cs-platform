from __future__ import annotations

from datetime import datetime, timezone

from .schemas import CampaignDefinition, CampaignEligibilityResult


def check_campaign_eligibility(
    campaign: CampaignDefinition,
    context: dict,
    now: datetime | None = None,
) -> CampaignEligibilityResult:
    current = now or datetime.now(timezone.utc)
    reasons: list[str] = []

    if not campaign.enabled:
        reasons.append("campaign_disabled")

    if current < campaign.valid_from or current > campaign.valid_to:
        reasons.append("campaign_outside_validity")

    journey = context.get("journey_stage")
    if campaign.eligible_journey_stages and journey not in campaign.eligible_journey_stages:
        reasons.append("journey_not_eligible")

    product_id = context.get("recent_product_id")
    if campaign.eligible_product_ids and product_id not in campaign.eligible_product_ids:
        reasons.append("product_not_eligible")

    if context.get("campaign_frequency_capped"):
        reasons.append("frequency_capped")

    if context.get("user_opted_out_campaigns"):
        reasons.append("user_opted_out")

    if context.get("active_complaint") or context.get("active_refund"):
        reasons.append("service_recovery_suppression")

    return CampaignEligibilityResult(eligible=not reasons, reason_codes=reasons)
