from __future__ import annotations

from .schemas import (
    ActionCandidate,
    BusinessAction,
    NextBestActionResult,
)
from .suppression import evaluate_global_suppression


class NextBestActionPolicy:
    version = "rules-v1"

    def decide(
        self,
        context: dict,
        candidates: list[ActionCandidate],
    ) -> NextBestActionResult:
        suppression = evaluate_global_suppression(context)
        no_action = ActionCandidate(
            action=BusinessAction.NO_PROACTIVE_ACTION,
            priority=0,
            confidence=1.0,
            optional=True,
            reason_codes=suppression.reason_codes or ["no_eligible_candidate"],
        )

        if suppression.suppressed:
            return NextBestActionResult(
                selected=no_action,
                candidates=candidates,
                suppressed_candidates=candidates,
                suppression_reason_codes=suppression.reason_codes,
                policy_version=self.version,
            )

        eligible = [
            candidate
            for candidate in candidates
            if not self._candidate_suppressed(context, candidate)
        ]
        if not eligible:
            return NextBestActionResult(
                selected=no_action,
                candidates=candidates,
                policy_version=self.version,
            )

        selected = sorted(
            eligible,
            key=lambda item: (item.priority, item.confidence),
            reverse=True,
        )[0]
        return NextBestActionResult(
            selected=selected,
            candidates=candidates,
            suppressed_candidates=[
                candidate for candidate in candidates if candidate not in eligible
            ],
            policy_version=self.version,
        )

    @staticmethod
    def _candidate_suppressed(
        context: dict,
        candidate: ActionCandidate,
    ) -> bool:
        if candidate.action == BusinessAction.MENTION_CAMPAIGN:
            if context.get("conversation_mode") == "SOCIAL_ONLY" and not context.get(
                "recent_product_context"
            ):
                return True
            if context.get("campaign_frequency_capped"):
                return True

        if context.get("same_action_shown_last_turn") == candidate.action:
            return True

        return False
