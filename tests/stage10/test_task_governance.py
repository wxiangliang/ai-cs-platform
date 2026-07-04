"""多任务治理单元测试：pending 入栈、追问上限、任务时效字段。"""

from app.chat.intent.types import DecisionSource, IntentLabel, IntentResult
from app.chat.state.manager import dialog_state_manager as m
from app.chat.state.types import DialogStateValue, TurnStatus
from app.core.config import settings


def _intent(label: str) -> IntentResult:
    return IntentResult(pred_label=label, confidence=0.9, decision_source=DecisionSource.RULE_KEYWORD)


def test_pending_intents_pushed_and_resumed():
    """多意图：主任务（读、槽位齐）完成后，pending 的物流任务恢复且带自己的槽位。"""
    r = m.resolve(
        current_state=DialogStateValue.IDLE,
        active_task=None,
        intent_result=_intent(IntentLabel.ORDER_QUERY_STATUS),
        slots={"order_id": "A1"},
        pending_intents=[{"intent": IntentLabel.LOGISTICS_TRACK, "slots": {"order_id": "B2"}}],
    )
    assert r.status == TurnStatus.DONE  # 主任务完成
    assert r.resumed_task is not None
    assert r.resumed_task["intent"] == IntentLabel.LOGISTICS_TRACK
    assert r.resumed_task["collected_slots"]["order_id"] == "B2"  # 槽位各归其主
    # 槽位已齐的读任务恢复为等待态（ready），下一轮续接执行
    assert r.resumed_task.get("ready") is True
    assert r.active_task is not None


def test_pending_kept_in_stack_while_primary_collecting():
    """主任务缺槽位 → pending 留在栈中等待。"""
    r = m.resolve(
        current_state=DialogStateValue.IDLE,
        active_task=None,
        intent_result=_intent(IntentLabel.AFTERSALE_REFUND),
        slots={},
        pending_intents=[{"intent": IntentLabel.LOGISTICS_TRACK, "slots": {}}],
    )
    assert r.status == TurnStatus.NEEDS_SLOT
    assert len(r.task_stack) == 1 and r.task_stack[0]["intent"] == IntentLabel.LOGISTICS_TRACK


def test_ask_count_gives_up(monkeypatch):
    """同一任务追问超过 TASK_MAX_ASKS → 放弃并置 gave_up。"""
    monkeypatch.setattr(settings, "TASK_MAX_ASKS", 2)
    task = None
    result = None
    for _ in range(3):
        result = m.resolve(
            current_state=DialogStateValue.COLLECTING if task else DialogStateValue.IDLE,
            active_task=task,
            intent_result=_intent(
                IntentLabel.META_UNKNOWN if task else IntentLabel.AFTERSALE_REFUND
            ),
            slots={},
            task_stack=[],
        )
        task = result.active_task
    assert result is not None and result.gave_up is True
    assert result.active_task is None and result.status == TurnStatus.FALLBACK


def test_tasks_carry_updated_ts():
    """任务每次评估都刷新 updated_ts（TTL 依据）。"""
    r = m.resolve(
        current_state=DialogStateValue.IDLE,
        active_task=None,
        intent_result=_intent(IntentLabel.AFTERSALE_REFUND),
        slots={},
    )
    assert r.active_task is not None and r.active_task.get("updated_ts", 0) > 0
