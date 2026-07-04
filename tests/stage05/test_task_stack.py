"""DialogStateManager 任务栈与执行交接单元测试（Stage 05）。"""

from app.chat.intent.types import DecisionSource, IntentLabel, IntentResult
from app.chat.state.manager import dialog_state_manager as m
from app.chat.state.types import DialogStateValue, TurnStatus


def _intent(label: str) -> IntentResult:
    return IntentResult(pred_label=label, confidence=0.9, decision_source=DecisionSource.RULE_KEYWORD)


def _refund_task(**extra):
    return {
        "intent": IntentLabel.AFTERSALE_REFUND,
        "kind": "write",
        "required_slots": ["order_id"],
        "collected_slots": {"order_id": "A1"},
        "task_id": "task-1",
        **extra,
    }


def test_confirm_hands_task_to_executor():
    r = m.resolve(
        current_state=DialogStateValue.CONFIRMING,
        active_task=_refund_task(),
        intent_result=_intent(IntentLabel.META_CONFIRM),
        slots={},
    )
    assert r.status == TurnStatus.CONFIRMED
    assert r.finished_task is not None and r.finished_task["task_id"] == "task-1"
    assert r.active_task is None


def test_interrupt_pushes_task_to_stack():
    """确认门中提出新业务意图 → 旧任务入栈。"""
    r = m.resolve(
        current_state=DialogStateValue.CONFIRMING,
        active_task=_refund_task(),
        intent_result=_intent(IntentLabel.LOGISTICS_TRACK),
        slots={"order_id": "A1"},
    )
    # 新任务是物流查询（读、槽位齐全）→ DONE；旧退款任务应已入栈并随即恢复
    assert r.status == TurnStatus.DONE
    assert r.resumed_task is not None
    assert r.resumed_task["intent"] == IntentLabel.AFTERSALE_REFUND
    # 恢复的退款任务槽位齐全 → 回到确认门
    assert r.new_state == DialogStateValue.CONFIRMING


def test_interrupt_with_pending_slots_keeps_stack():
    """新任务缺槽位（旧任务也提供不了）→ 旧任务留在栈中等待。"""
    r = m.resolve(
        current_state=DialogStateValue.CONFIRMING,
        active_task=_refund_task(),
        # 问价需要 product_name，退款任务只有 order_id，无从继承
        intent_result=_intent(IntentLabel.PRODUCT_ASK_PRICE),
        slots={},
    )
    assert r.status == TurnStatus.NEEDS_SLOT
    assert len(r.task_stack) == 1
    assert r.task_stack[0]["intent"] == IntentLabel.AFTERSALE_REFUND


def test_resume_after_deny():
    """否认当前任务后，从栈中恢复挂起任务。"""
    suspended = {
        "intent": IntentLabel.ORDER_CANCEL, "kind": "write",
        "required_slots": ["order_id"], "collected_slots": {"order_id": "B2"},
        "task_id": "task-2",
    }
    r = m.resolve(
        current_state=DialogStateValue.CONFIRMING,
        active_task=_refund_task(),
        intent_result=_intent(IntentLabel.META_DENY),
        slots={},
        task_stack=[suspended],
    )
    assert r.status == TurnStatus.ABORTED
    assert r.resumed_task is not None and r.resumed_task["intent"] == IntentLabel.ORDER_CANCEL
    assert r.new_state == DialogStateValue.CONFIRMING  # 恢复任务槽位齐全 → 确认门
    assert r.task_stack == []


def test_abort_clears_stack():
    """「算了」放弃一切：当前任务与挂起栈全部清空。"""
    r = m.resolve(
        current_state=DialogStateValue.CONFIRMING,
        active_task=_refund_task(),
        intent_result=_intent(IntentLabel.META_ABORT),
        slots={},
        task_stack=[{"intent": "X", "task_id": "t9"}],
    )
    assert r.new_state == DialogStateValue.ABORTED
    assert r.task_stack == [] and r.active_task is None


def test_stack_depth_capped(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "TASK_STACK_MAX", 2)
    stack = [{"intent": "OLD1", "task_id": "t1"}, {"intent": "OLD2", "task_id": "t2"}]
    r = m.resolve(
        current_state=DialogStateValue.COLLECTING,
        active_task=_refund_task(),
        # 问价缺 product_name（不可继承）→ 新任务停在补槽，栈不弹出
        intent_result=_intent(IntentLabel.PRODUCT_ASK_PRICE),
        slots={},
        task_stack=stack,
    )
    # 溢出丢最旧：栈内应只剩 OLD2 + 刚挂起的退款
    assert len(r.task_stack) == 2
    assert [t["intent"] for t in r.task_stack] == ["OLD2", IntentLabel.AFTERSALE_REFUND]


def test_interrupt_inherits_context_slots():
    """「先帮我查下这个订单的物流」：新任务从被挂起任务继承 order_id。"""
    r = m.resolve(
        current_state=DialogStateValue.CONFIRMING,
        active_task=_refund_task(),
        intent_result=_intent(IntentLabel.LOGISTICS_TRACK),
        slots={},  # 本轮没显式给订单号
    )
    # 物流任务继承退款任务的 order_id=A1 → 槽位齐全直接 DONE，随即恢复退款确认
    assert r.status == TurnStatus.DONE
    assert r.resumed_task is not None and r.resumed_task["intent"] == IntentLabel.AFTERSALE_REFUND
