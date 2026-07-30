"""Stage 26 补槽守护测试（P1）：pending-slot 定向提取 + 分类器接入。

覆盖 stage-26 文档 5.2 用例表「补槽不误切」正例与「防误填」反例，
以及控制语义优先的顺序红线。全部纯规则，无模型/LLM 依赖。
"""

import pytest

from app.chat.intent.rule_classifier import rule_intent_classifier
from app.chat.intent.types import DecisionSource, IntentLabel
from app.chat.slots.pending_fill import try_fill_pending_slot
from app.chat.state.types import DialogStateValue

_COLLECTING = DialogStateValue.COLLECTING


# ---------------- 定向提取单元：正例 ----------------


@pytest.mark.parametrize(
    "text",
    [
        "12345678",
        "订单号 12345678",
        "订单号是12345678",
        "是12345678",
        "应该是12345678",
        "我的订单号为12345678",
        "单号：12345678",
        "订单号是12345678，麻烦快点",
    ],
)
def test_fill_order_id_positive(text):
    result = try_fill_pending_slot(text, "order_id")
    assert result is not None, text
    assert result.slot == "order_id"
    assert result.value == "12345678"


def test_fill_evidence_levels():
    assert try_fill_pending_slot("订单号是12345678", "order_id").evidence == "explicit_slot_name"
    assert try_fill_pending_slot("12345678", "order_id").evidence == "pure_value"
    assert try_fill_pending_slot("是12345678", "order_id").evidence == "contextual_answer"


def test_fill_phone_positive():
    result = try_fill_pending_slot("手机号是13800138000", "phone")
    assert result is not None
    assert result.value == "13800138000"
    assert result.evidence == "explicit_slot_name"


# ---------------- 定向提取单元：防误填反例 ----------------


@pytest.mark.parametrize(
    "text",
    [
        "手机号是13800138000",  # 防误填 2：显式说了别的字段名
        "13800138000",  # 防误填 1：手机号形状不填 order_id
        "金额是12345678",  # 防误填 2：金额字段
        "我有12345678个问题",  # 防误填 3：量词紧跟
        "先不退款了",  # 无值
        "帮我查物流",  # 无值
        "12345678帮我查下物流到哪了",  # 防误填 4：残句带新诉求
        "你们支持开发票吗",  # 无值
        "please",  # 纯字母不含数字
    ],
)
def test_fill_order_id_negative(text):
    assert try_fill_pending_slot(text, "order_id") is None, text


def test_fill_unknown_slot_type_returns_none():
    """不认识的槽位类型宁缺勿误填。"""
    assert try_fill_pending_slot("红色的", "color") is None


def test_fill_value_already_collected_for_other_slot():
    """值已作为别的槽位存过（复述已给的手机号）→ 不填。"""
    assert (
        try_fill_pending_slot("A123456789", "order_id", {"phone": "A123456789"}) is None
    )


# ---------------- 分类器接入：控制层顺序红线 ----------------


def _ctl(text, pending_slot="order_id", state=_COLLECTING):
    return rule_intent_classifier.classify_control(
        text, current_state=state, has_active_task=True, pending_slot=pending_slot
    )


def test_pending_fill_hits_in_collecting():
    result = _ctl("订单号是12345678")
    assert result is not None
    assert result.pred_label == IntentLabel.META_SLOT_ONLY
    assert result.decision_source == DecisionSource.RULE_PENDING_SLOT
    assert result.pending_fill == {
        "slot": "order_id",
        "value": "12345678",
        "evidence": "explicit_slot_name",
    }


def test_pending_fill_only_in_collecting():
    """非 COLLECTING 状态不做定向提取（IDLE 下「订单号是…」交语义层）。"""
    assert _ctl("订单号是12345678", state=DialogStateValue.IDLE) is None


def test_pending_fill_not_without_pending_slot():
    assert _ctl("订单号是12345678", pending_slot=None) is None


def test_control_semantics_take_priority_over_fill():
    """顺序红线：显式控制语义先于补槽提取。"""
    # 任务中途否定（不含数字）仍走 RULE_TASK_DENY
    r = _ctl("不是要退货")
    assert r is not None and r.decision_source == DecisionSource.RULE_TASK_DENY
    # 转人工不被吞
    r = _ctl("转人工")
    assert r is not None and r.pred_label == IntentLabel.META_TRANSFER_HUMAN
    # 纯放弃不被吞
    r = _ctl("算了不用了")
    assert r is not None and r.pred_label == IntentLabel.META_ABORT
    # 纯槽位输入维持原 decision_source（零回归：裸值不改道）
    r = _ctl("12345678")
    assert r is not None and r.decision_source == DecisionSource.RULE_SLOT_ONLY


async def test_rule_classify_passthrough():
    """RuleIntentClassifier.classify 透传 pending 参数。"""
    result = await rule_intent_classifier.classify(
        "订单号是12345678",
        current_state=_COLLECTING,
        has_active_task=True,
        pending_slot="order_id",
    )
    assert result.decision_source == DecisionSource.RULE_PENDING_SLOT
