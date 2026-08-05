from __future__ import annotations

from app.chat.playbooks.schemas import (
    PlaybookEvent,
    PlaybookInstance,
    PlaybookResult,
    PlaybookStatus,
    ResponseDirective,
    ToolRequest,
)


class PurchaseAssistSkill:
    code = "PURCHASE_ASSIST"
    version = 1

    def handle(
        self,
        instance: PlaybookInstance,
        event: PlaybookEvent,
        context: dict,
    ) -> PlaybookResult:
        if context.get("active_complaint") or context.get("active_refund"):
            return PlaybookResult(
                status=PlaybookStatus.SUSPENDED,
                reason_codes=["service_recovery_has_priority"],
            )

        blocker = instance.slots.get("purchase_blocker")
        if not blocker:
            return PlaybookResult(
                status=PlaybookStatus.WAITING_USER,
                next_step="IDENTIFY_BLOCKER",
                response_directives=[
                    ResponseDirective(
                        type="ASK_PURCHASE_BLOCKER",
                        payload={"allowed": [
                            "PRICE",
                            "FEATURE_UNCERTAINTY",
                            "COMPATIBILITY",
                            "DELIVERY_TIME",
                            "RETURN_POLICY",
                            "TRUST",
                            "PAYMENT_METHOD",
                        ]},
                    )
                ],
                reason_codes=["purchase_blocker_unknown"],
            )

        return PlaybookResult(
            status=PlaybookStatus.WAITING_TOOL,
            next_step="RESOLVE_BLOCKER",
            tool_requests=[
                ToolRequest(
                    tool_name="resolve_purchase_blocker",
                    arguments={
                        "product_id": instance.slots.get("product_id"),
                        "blocker": blocker,
                    },
                    readonly=True,
                )
            ],
            reason_codes=["purchase_blocker_identified"],
        )
