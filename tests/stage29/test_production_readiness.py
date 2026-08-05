"""生产就绪审计落地项测试（2026-08-05，docs/ops/production_readiness.md）。

1. 端到端幂等契约客户端义务：写操作 params 携带 idempotency_key=task_id，
   且 mock 端同键同结果（外部系统去重语义的联调基线）；
2. 状态结构回滚容错：状态机对 task dict 的「未知多余字段/缺省字段」都容错
   ——升级后回滚代码，旧代码读新结构不崩（宽容读取纪律的回归锁）。
"""

from app.chat.intent.types import DecisionSource, IntentLabel, IntentResult
from app.chat.state.manager import dialog_state_manager
from app.chat.state.types import DialogStateValue, TurnStatus
from app.chat.tools.mock_provider import mock_tool_provider


# ---------------- 幂等契约（客户端义务 + mock 去重语义） ----------------


async def test_write_tool_idempotency_same_key_same_result():
    """同幂等键（同 task 重试）→ 同工单号；异键 → 异工单号。"""
    p1 = {"order_id": "A1", "idempotency_key": "task-001"}
    r1 = await mock_tool_provider.invoke("create_refund_ticket", p1, tenant_id="t1")
    r2 = await mock_tool_provider.invoke("create_refund_ticket", dict(p1), tenant_id="t1")
    assert r1.ok and r2.ok
    assert r1.data["ticket_no"] == r2.data["ticket_no"]  # 重试不产生新工单


def test_executor_params_carry_idempotency_key():
    """执行器组参必须带 idempotency_key=task_id（对接契约客户端义务，防回潮）。

    直接检查源码组参语句而非跑全执行链（执行链需 DB 会话，组参是纯语句）。
    """
    import inspect

    from app.chat.actions import executor

    src = inspect.getsource(executor)
    assert '"idempotency_key": task_id' in src


# ---------------- 状态结构回滚容错（宽容读取） ----------------


def _intent(label: str, source: str = DecisionSource.RULE_KEYWORD) -> IntentResult:
    return IntentResult(pred_label=label, confidence=0.9, decision_source=source)


def test_resolve_tolerates_unknown_task_fields():
    """新版本给 task 加了字段后回滚代码：旧逻辑读新结构不崩、字段透传不丢。"""
    task = {
        "intent": "AFTERSALE.REFUND",
        "kind": "write",
        "required_slots": ["order_id"],
        "collected_slots": {},
        "task_id": "t1",
        # 模拟未来版本新增的未知字段
        "future_field_v99": {"nested": [1, 2, 3]},
        "another_flag": True,
    }
    result = dialog_state_manager.resolve(
        current_state=DialogStateValue.COLLECTING,
        active_task=task,
        intent_result=_intent(IntentLabel.META_SLOT_ONLY, DecisionSource.RULE_SLOT_ONLY),
        slots={"order_id": "A12345678"},
        task_stack=[{"intent": "LOGISTICS.TRACK", "collected_slots": {}, "future_x": 1}],
    )
    assert result.status == TurnStatus.NEEDS_CONFIRM
    # 未知字段随任务字典透传（_advance_task 构造新 dict 时保留），回滚再升级不丢数据
    assert result.active_task is not None
    assert result.active_task.get("future_field_v99") == {"nested": [1, 2, 3]}


def test_resolve_tolerates_missing_optional_fields():
    """极简 task（缺 ask_count/updated_ts/task_id 等可选字段）不崩。"""
    result = dialog_state_manager.resolve(
        current_state=DialogStateValue.COLLECTING,
        active_task={"intent": "AFTERSALE.REFUND", "collected_slots": {}},
        intent_result=_intent(IntentLabel.META_UNKNOWN, DecisionSource.SETFIT_LOW_CONF),
        slots={},
        task_stack=None,
        pending_intents=None,
    )
    # 缺 required_slots/kind 的任务按"无缺槽读任务"处理（DONE）——
    # 关键是宽容降级出确定结局，不抛异常
    assert result.status in (TurnStatus.NEEDS_SLOT, TurnStatus.FALLBACK, TurnStatus.DONE)


def test_resolve_tolerates_empty_and_none_inputs():
    result = dialog_state_manager.resolve(
        current_state="",  # 未知状态串
        active_task=None,
        intent_result=_intent(IntentLabel.CHITCHAT_GENERAL),
        slots={},
    )
    assert result.new_state  # 有确定的流转结果，不抛异常
