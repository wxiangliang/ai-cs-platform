from __future__ import annotations

from .schemas import JourneyStage, JourneyTransition


class JourneyManager:
    """Conservative journey transitions.

    Strong transactional facts may persist immediately. Soft conversational
    signals should remain candidates until enough evidence is collected.
    """

    def evaluate(
        self,
        current: JourneyStage,
        event: dict,
    ) -> JourneyTransition | None:
        event_type = event.get("type")

        if event_type == "ACCOUNT_CREATED":
            return JourneyTransition(
                from_stage=current,
                to_stage=JourneyStage.REGISTERED,
                confidence=1.0,
                evidence_codes=["account_created_fact"],
                persist=True,
            )

        if event_type == "ORDER_PAID":
            return JourneyTransition(
                from_stage=current,
                to_stage=JourneyStage.PURCHASED,
                confidence=1.0,
                evidence_codes=["order_paid_fact"],
                persist=True,
            )

        if event_type == "REQUIREMENTS_CONFIRMED" and current in {
            JourneyStage.DISCOVERING,
            JourneyStage.REGISTERED,
        }:
            return JourneyTransition(
                from_stage=current,
                to_stage=JourneyStage.CONSIDERING,
                confidence=0.8,
                evidence_codes=["requirements_confirmed"],
                persist=False,
            )

        return None
