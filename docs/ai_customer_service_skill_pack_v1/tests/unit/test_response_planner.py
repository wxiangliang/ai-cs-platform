from app.chat.response.planner import ResponsePlanner
from app.chat.response.schemas import ResponsePart


def test_confirmation_drops_optional_action() -> None:
    planner = ResponsePlanner()
    plan = planner.plan(
        parts=[
            ResponsePart(
                type="CONFIRMATION",
                priority=100,
                required=True,
            ),
            ResponsePart(
                type="OPTIONAL_SUGGESTION",
                priority=20,
                required=False,
            ),
        ],
        context={"confirmation_pending": True},
    )
    assert [item.type for item in plan.parts] == ["CONFIRMATION"]
    assert [item.type for item in plan.dropped_parts] == ["OPTIONAL_SUGGESTION"]
