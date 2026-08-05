from __future__ import annotations

from app.chat.playbooks.schemas import (
    PlaybookEvent,
    PlaybookInstance,
    PlaybookResult,
    PlaybookStatus,
    ResponseDirective,
    ToolRequest,
)


class ProductDiscoverySkill:
    code = "PRODUCT_DISCOVERY"
    version = 1
    REQUIRED_SLOTS = ("category", "budget_range", "usage_scenario")

    def handle(
        self,
        instance: PlaybookInstance,
        event: PlaybookEvent,
        context: dict,
    ) -> PlaybookResult:
        missing = [slot for slot in self.REQUIRED_SLOTS if not instance.slots.get(slot)]

        if missing:
            slot = self._select_next_slot(missing, context)
            return PlaybookResult(
                status=PlaybookStatus.WAITING_USER,
                next_step="COLLECT_HARD_CONSTRAINTS",
                response_directives=[
                    ResponseDirective(
                        type="ASK_SLOT",
                        payload={"slot": slot},
                    )
                ],
                reason_codes=["missing_required_slot", f"selected_slot:{slot}"],
            )

        return PlaybookResult(
            status=PlaybookStatus.WAITING_TOOL,
            next_step="PRESENT_OPTIONS",
            tool_requests=[
                ToolRequest(
                    tool_name="recommend_products",
                    arguments={
                        "category": instance.slots["category"],
                        "hard_constraints": {
                            "budget_range": instance.slots["budget_range"],
                            "usage_scenario": instance.slots["usage_scenario"],
                        },
                    },
                    readonly=True,
                )
            ],
            reason_codes=["required_slots_complete"],
        )

    @staticmethod
    def _select_next_slot(missing: list[str], context: dict) -> str:
        # Replace with category-specific information-gain configuration.
        priority = ["category", "budget_range", "usage_scenario"]
        return next(slot for slot in priority if slot in missing)
