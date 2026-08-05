from __future__ import annotations

from .schemas import ResponsePart, ResponsePlan


class ResponsePlanner:
    def plan(
        self,
        parts: list[ResponsePart],
        context: dict,
        max_optional_parts: int = 1,
    ) -> ResponsePlan:
        kept: list[ResponsePart] = []
        dropped: list[ResponsePart] = []
        optional_count = 0

        for part in sorted(parts, key=lambda item: item.priority, reverse=True):
            if part.required:
                kept.append(part)
                continue

            if context.get("confirmation_pending"):
                dropped.append(part)
                continue

            if context.get("sentiment") in {"STRONG_NEGATIVE", "ANGRY"}:
                dropped.append(part)
                continue

            if optional_count >= max_optional_parts:
                dropped.append(part)
                continue

            kept.append(part)
            optional_count += 1

        return ResponsePlan(
            parts=kept,
            dropped_parts=dropped,
            reason_codes=["priority_order_applied", "optional_cap_applied"],
        )
