from __future__ import annotations

from app.chat.playbooks.schemas import (
    ActionRequest,
    PlaybookEvent,
    PlaybookInstance,
    PlaybookResult,
    PlaybookStatus,
    ResponseDirective,
    ToolRequest,
)


class NewUserOnboardingSkill:
    code = "NEW_USER_ONBOARDING"
    version = 1

    def handle(
        self,
        instance: PlaybookInstance,
        event: PlaybookEvent,
        context: dict,
    ) -> PlaybookResult:
        step = instance.current_step

        if event.type == "USER_ABORTED":
            return PlaybookResult(
                status=PlaybookStatus.ABORTED,
                reason_codes=["user_aborted"],
            )

        if step == "CHECK_STATUS":
            return PlaybookResult(
                status=PlaybookStatus.WAITING_TOOL,
                next_step="CHOOSE_CHANNEL",
                tool_requests=[
                    ToolRequest(
                        tool_name="get_registration_status",
                        arguments={"customer_code": instance.customer_code},
                        readonly=True,
                    )
                ],
                reason_codes=["registration_status_required"],
            )

        if step == "CHOOSE_CHANNEL":
            return PlaybookResult(
                status=PlaybookStatus.WAITING_USER,
                next_step="COLLECT_IDENTIFIER",
                response_directives=[
                    ResponseDirective(
                        type="ASK_CHOICE",
                        payload={"slot": "registration_channel", "choices": ["PHONE", "EMAIL"]},
                    )
                ],
                reason_codes=["registration_channel_missing"],
            )

        if step == "CONFIRM_CREATE_ACCOUNT":
            return PlaybookResult(
                status=PlaybookStatus.WAITING_CONFIRMATION,
                next_step="CREATE_ACCOUNT",
                action_request=ActionRequest(
                    action_code="CREATE_ACCOUNT",
                    arguments=instance.slots,
                    idempotency_key=instance.instance_id,
                    requires_confirmation=True,
                ),
                reason_codes=["write_action_requires_confirmation"],
            )

        raise NotImplementedError(f"Implement onboarding step: {step}")
