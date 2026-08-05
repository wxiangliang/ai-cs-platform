from __future__ import annotations

from .schemas import SuppressionResult

HIGH_RISK_INTENT_PREFIXES = (
    "RETURN.",
    "COMPLAINT.",
    "ORDER.CANCEL",
    "PRIVACY.",
)


def evaluate_global_suppression(context: dict) -> SuppressionResult:
    reasons: list[str] = []

    intent = str(context.get("active_intent") or "")
    if intent.startswith(HIGH_RISK_INTENT_PREFIXES):
        reasons.append("high_risk_active_intent")

    if context.get("confirmation_pending"):
        reasons.append("confirmation_pending")

    if context.get("human_handoff_active"):
        reasons.append("human_handoff_active")

    if context.get("sentiment") in {"STRONG_NEGATIVE", "ANGRY"}:
        reasons.append("negative_sentiment")

    if context.get("user_opted_out_proactive"):
        reasons.append("user_opted_out")

    if context.get("tool_facts_unavailable"):
        reasons.append("required_facts_unavailable")

    return SuppressionResult(suppressed=bool(reasons), reason_codes=reasons)
