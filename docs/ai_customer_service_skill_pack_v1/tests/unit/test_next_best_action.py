from app.chat.policy.next_best_action import NextBestActionPolicy
from app.chat.policy.schemas import ActionCandidate, BusinessAction


def test_refund_suppresses_campaign() -> None:
    policy = NextBestActionPolicy()
    result = policy.decide(
        context={"active_intent": "RETURN.REFUND"},
        candidates=[
            ActionCandidate(
                action=BusinessAction.MENTION_CAMPAIGN,
                priority=90,
                reason_codes=["campaign_eligible"],
            )
        ],
    )
    assert result.selected.action == BusinessAction.NO_PROACTIVE_ACTION
    assert "high_risk_active_intent" in result.suppression_reason_codes


def test_selects_highest_priority_eligible_action() -> None:
    policy = NextBestActionPolicy()
    result = policy.decide(
        context={},
        candidates=[
            ActionCandidate(
                action=BusinessAction.OFFER_PRODUCT_COMPARE,
                priority=50,
            ),
            ActionCandidate(
                action=BusinessAction.START_PRODUCT_DISCOVERY,
                priority=60,
            ),
        ],
    )
    assert result.selected.action == BusinessAction.START_PRODUCT_DISCOVERY


def test_social_without_product_context_suppresses_campaign() -> None:
    policy = NextBestActionPolicy()
    result = policy.decide(
        context={
            "conversation_mode": "SOCIAL_ONLY",
            "recent_product_context": None,
        },
        candidates=[
            ActionCandidate(
                action=BusinessAction.MENTION_CAMPAIGN,
                priority=80,
            )
        ],
    )
    assert result.selected.action == BusinessAction.NO_PROACTIVE_ACTION
