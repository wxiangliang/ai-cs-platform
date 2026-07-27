"""Stage 23 对话方向纠偏测试（纯规则通道，无 LLM 依赖）。

覆盖：任务中途否定判定 / 状态机仅终止当前任务 / 重定向话术 /
零进度恢复降调 / 中置信软确认触发与豁免。
"""

from app.chat.graph.nodes.response_generate import response_generate
from app.chat.graph.nodes.save_turn import _resume_note
from app.chat.intent.rule_classifier import rule_intent_classifier
from app.chat.intent.types import DecisionSource, IntentLabel, IntentResult
from app.chat.state.manager import dialog_state_manager
from app.chat.state.types import DialogStateValue, TurnStatus
from app.core.config import settings

_COLLECTING = DialogStateValue.COLLECTING


def _ctl(text, state=_COLLECTING):
    return rule_intent_classifier.classify_control(text, current_state=state)


# ---------------- 任务中途否定判定（规则层） ----------------


def test_task_deny_detected_in_collecting():
    for text in ["不是要退货", "我不是要退货", "不对", "不是", "搞错了", "你理解错了", "不是这个意思"]:
        result = _ctl(text)
        assert result is not None and result.pred_label == IntentLabel.META_DENY, text
        assert result.decision_source == DecisionSource.RULE_TASK_DENY, text


def test_task_deny_guards():
    # 含数字 → 槽位纠正，不判否定
    assert _ctl("不对，是A12345678") is None or _ctl("不对，是A12345678").pred_label != IntentLabel.META_DENY
    # 带新诉求的长句 → 放行语义层（拆新意图挂起任务）
    r = _ctl("不是要退货，我想查物流")
    assert r is None or r.pred_label != IntentLabel.META_DENY
    # 仅 COLLECTING 状态生效
    assert _ctl("不是要退货", state=DialogStateValue.IDLE) is None
    # CONFIRMING 下仍走确认门原逻辑（decision_source 不同）
    r = _ctl("不对", state=DialogStateValue.CONFIRMING)
    assert r is not None and r.decision_source == DecisionSource.RULE_CONFIRM_GATE


# ---------------- 状态机：仅终止当前任务，栈照常恢复 ----------------


def _deny_intent():
    return IntentResult(
        pred_label=IntentLabel.META_DENY, confidence=0.9,
        decision_source=DecisionSource.RULE_TASK_DENY,
    )


def test_state_machine_collecting_deny_terminates_only_current():
    task = {"intent": "AFTERSALE.RETURN", "collected_slots": {}, "task_id": "t1"}
    suspended = {"intent": "LOGISTICS.TRACK", "collected_slots": {"order_id": "A1"}, "task_id": "t2"}
    result = dialog_state_manager.resolve(
        current_state=_COLLECTING, active_task=task,
        intent_result=_deny_intent(), slots={}, task_stack=[suspended],
    )
    assert result.status == TurnStatus.ABORTED
    assert result.denied_task == {"intent": "AFTERSALE.RETURN"}
    # 栈中任务照常恢复（不像 META.ABORT 清空一切）
    assert result.resumed_task is not None
    assert result.resumed_task.get("intent") == "LOGISTICS.TRACK"


def test_state_machine_collecting_deny_without_stack():
    task = {"intent": "AFTERSALE.RETURN", "collected_slots": {}}
    result = dialog_state_manager.resolve(
        current_state=_COLLECTING, active_task=task,
        intent_result=_deny_intent(), slots={}, task_stack=[],
    )
    assert result.status == TurnStatus.ABORTED
    assert result.active_task is None
    assert result.denied_task == {"intent": "AFTERSALE.RETURN"}


def test_confirming_deny_unchanged():
    """CONFIRMING 否认行为不变（不带 denied_task 标记）。"""
    task = {"intent": "AFTERSALE.RETURN", "collected_slots": {"order_id": "A1"}}
    result = dialog_state_manager.resolve(
        current_state=DialogStateValue.CONFIRMING, active_task=task,
        intent_result=_deny_intent(), slots={}, task_stack=[],
    )
    assert result.status == TurnStatus.ABORTED
    assert result.denied_task is None


# ---------------- 回复渲染 ----------------


async def test_denied_redirect_reply():
    state = {
        "status": TurnStatus.ABORTED,
        "denied_task": {"intent": "AFTERSALE.RETURN"},
        "intent_result": {"pred_label": IntentLabel.META_DENY},
    }
    result = await response_generate(state)
    assert result["graph_trace"] == ["response_generate:task_denied"]
    assert "先不办理" in result["reply"]
    assert "想咨询或办理什么" in result["reply"]


async def test_soft_confirm_prefix_mid_confidence():
    """SETFIT 中置信新开任务 → 追问带意图复述前缀。"""
    state = {
        "status": TurnStatus.NEEDS_SLOT,
        "active_task": {"intent": "AFTERSALE.RETURN", "collected_slots": {}},
        "missing_slot": "order_id",
        "intent_result": {
            "pred_label": "AFTERSALE.RETURN", "final_intent": "AFTERSALE.RETURN",
            "confidence": 0.45, "decision_source": DecisionSource.SETFIT,
        },
    }
    result = await response_generate(state)
    assert "对吗" in result["reply"]  # 意图复述前缀在场
    assert "如果不是" in result["reply"]


async def test_soft_confirm_exemptions():
    """高置信 / 规则来源 / 恢复任务不加前缀。"""
    base = {
        "status": TurnStatus.NEEDS_SLOT,
        "active_task": {"intent": "AFTERSALE.RETURN", "collected_slots": {}},
        "intent_result": {
            "pred_label": "AFTERSALE.RETURN", "final_intent": "AFTERSALE.RETURN",
            "confidence": 0.85, "decision_source": DecisionSource.SETFIT,
        },
    }
    result = await response_generate(dict(base))
    assert "对吗" not in result["reply"]  # 高置信

    rule = dict(base)
    rule["intent_result"] = {**base["intent_result"], "confidence": 0.9,
                             "decision_source": DecisionSource.RULE_KEYWORD}
    result = await response_generate(rule)
    assert "对吗" not in result["reply"]  # 规则来源

    resumed = dict(base)
    resumed["intent_result"] = {**base["intent_result"], "confidence": 0.45}
    resumed["resumed_task"] = {"intent": "AFTERSALE.RETURN"}
    result = await response_generate(resumed)
    assert "对吗" not in result["reply"]  # 恢复任务


# ---------------- 零进度恢复降调 ----------------


def test_resume_note_zero_progress_optional():
    state = {
        "resumed_task": {"intent": "AFTERSALE.RETURN", "collected_slots": {}, "task_id": "t9"},
        "new_state": DialogStateValue.COLLECTING,
    }
    note = _resume_note(state)
    assert "还没有开始办理" in note
    assert "不是要办这个" in note  # 给出与否定通道一致的退出话术


def test_resume_note_with_progress_unchanged():
    state = {
        "resumed_task": {
            "intent": "AFTERSALE.RETURN",
            "collected_slots": {"order_id": "A1"},
            "task_id": "t9",
        },
        "new_state": DialogStateValue.COLLECTING,
    }
    note = _resume_note(state)
    assert "我们继续您刚才的" in note  # 有进度维持原话术
    assert "还没有开始办理" not in note


def test_soft_confirm_threshold_config():
    assert 0 < settings.INTENT_SOFT_CONFIRM_THRESHOLD <= 1
