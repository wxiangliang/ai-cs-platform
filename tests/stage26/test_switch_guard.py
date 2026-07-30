"""Stage 26 切换守护与 UNKNOWN 续接收紧测试（P3，状态机层）。

覆盖 stage-26 文档 5.2：低置信新意图不切+二选一澄清 / 高置信+margin 达标可切 /
显式信号词普通阈值可切 / CONFIRMING 更高门槛 / UNKNOWN 证据过滤与防污染 /
既有语义零回归（确认门、任务否定、挂起恢复）。
"""

from app.chat.intent.types import DecisionSource, IntentLabel, IntentResult
from app.chat.state.manager import SWITCH_SIGNAL_RE, dialog_state_manager
from app.chat.state.types import DialogStateValue, TurnStatus

_COLLECTING = DialogStateValue.COLLECTING
_CONFIRMING = DialogStateValue.CONFIRMING


def _task(intent="AFTERSALE.REFUND", collected=None):
    return {
        "intent": intent,
        "kind": "write",
        "required_slots": ["order_id"],
        "collected_slots": dict(collected or {}),
        "task_id": "t1",
        "ask_count": 0,
    }


def _intent(label, conf, source=DecisionSource.SETFIT, margin=None):
    return IntentResult(
        pred_label=label, confidence=conf, decision_source=source, margin=margin
    )


def _resolve(state, task, intent_result, slots=None, explicit_switch=False, stack=None):
    return dialog_state_manager.resolve(
        current_state=state,
        active_task=task,
        intent_result=intent_result,
        slots=slots or {},
        task_stack=stack or [],
        explicit_switch=explicit_switch,
    )


# ---------------- 切换守护 ----------------


def test_collecting_low_conf_switch_guarded():
    """补槽中 SetFit 中置信新意图 → 不切换，保留任务 + switch_candidate。"""
    r = _resolve(_COLLECTING, _task(), _intent("ORDER.QUERY_STATUS", 0.65))
    assert r.switch_candidate == "ORDER.QUERY_STATUS"
    assert r.active_task is not None
    assert r.active_task["intent"] == "AFTERSALE.REFUND"
    assert r.status == TurnStatus.NEEDS_SLOT
    # 计一次追问防无限僵持
    assert r.active_task["ask_count"] == 1


def test_collecting_high_conf_switch_allowed():
    """高置信+margin 达标的真实新意图仍能正常挂起切换（守护不是禁切换）。"""
    r = _resolve(
        _COLLECTING, _task(), _intent("LOGISTICS.TRACK", 0.90, margin=0.50)
    )
    assert r.switch_candidate is None
    assert r.active_task is not None
    assert r.active_task["intent"] == "LOGISTICS.TRACK"
    # 原任务挂起入栈
    assert any(t.get("intent") == "AFTERSALE.REFUND" for t in r.task_stack)


def test_collecting_high_conf_small_margin_guarded():
    """置信过线但 margin 小 → 仍拦（二维判据）。"""
    r = _resolve(
        _COLLECTING,
        _task(),
        _intent("ORDER.QUERY_STATUS", 0.85, DecisionSource.SETFIT_LOW_MARGIN, margin=0.03),
    )
    assert r.switch_candidate == "ORDER.QUERY_STATUS"


def test_explicit_switch_signal_bypasses_guard():
    """显式切换信号（「顺便」）→ 普通阈值放行。"""
    r = _resolve(
        _COLLECTING, _task(), _intent("LOGISTICS.TRACK", 0.65), explicit_switch=True
    )
    assert r.switch_candidate is None
    assert r.active_task["intent"] == "LOGISTICS.TRACK"


def test_confirming_higher_threshold():
    """CONFIRMING 门槛更高：0.80 在 COLLECTING 可切、CONFIRMING 被拦。"""
    intent = _intent("LOGISTICS.TRACK", 0.80, margin=0.40)
    assert _resolve(_COLLECTING, _task(), intent).switch_candidate is None
    r = _resolve(_CONFIRMING, _task(collected={"order_id": "A1"}), intent)
    assert r.switch_candidate == "LOGISTICS.TRACK"
    # CONFIRMING 拦截轮重发确认，不动已收集槽位
    assert r.status == TurnStatus.NEEDS_CONFIRM
    assert r.active_task["collected_slots"] == {"order_id": "A1"}


def test_rule_keyword_switch_passes():
    """规则关键词来源（控制层取消订单，置信 0.9）确定性命中不拦。"""
    r = _resolve(
        _COLLECTING, _task(), _intent("ORDER.CANCEL", 0.9, DecisionSource.RULE_KEYWORD)
    )
    assert r.switch_candidate is None
    assert r.active_task["intent"] == "ORDER.CANCEL"


def test_same_intent_reexpression_not_guarded():
    """同意图重复表达走原有重建逻辑，不触发守护。"""
    r = _resolve(_COLLECTING, _task(), _intent("AFTERSALE.REFUND", 0.5))
    assert r.switch_candidate is None
    assert r.active_task["intent"] == "AFTERSALE.REFUND"


def test_switch_signal_regex():
    assert SWITCH_SIGNAL_RE.search("顺便帮我查下物流")
    assert SWITCH_SIGNAL_RE.search("另外我还想问下发票")
    assert not SWITCH_SIGNAL_RE.search("订单号是12345678")


# ---------------- UNKNOWN 续接收紧 ----------------


def _unknown():
    return _intent(IntentLabel.META_UNKNOWN, 0.3, DecisionSource.SETFIT_LOW_CONF)


def test_unknown_with_evidence_merges_only_pending_slot():
    """非严格槽位有续接证据：只并入缺失槽位的值，其余抽取结果不并入。

    （缺 color 时「红色的」被判 UNKNOWN——color 无严格校验、无法被补槽守护
    接走，抽取值作为续接证据并入是合理路径）
    """
    task = {
        "intent": "AFTERSALE.EXCHANGE",
        "kind": "write",
        "required_slots": ["order_id", "color"],
        "collected_slots": {"order_id": "A12345678"},
        "task_id": "t1",
        "ask_count": 0,
    }
    r = _resolve(_COLLECTING, task, _unknown(), slots={"color": "红", "quantity": 3})
    assert r.unknown_with_task is False
    # order_id 已有 + color 并入 → 写操作进确认门；quantity 非必填不并入
    assert r.status == TurnStatus.NEEDS_CONFIRM
    assert r.active_task["collected_slots"].get("color") == "红"
    assert "quantity" not in r.active_task["collected_slots"]


def test_unknown_without_evidence_holds_task():
    """无证据（「你们支持开发票吗」被判 UNKNOWN）：不填槽不切换，标记二选一澄清。"""
    r = _resolve(_COLLECTING, _task(), _unknown(), slots={})
    assert r.unknown_with_task is True
    assert r.status == TurnStatus.NEEDS_SLOT
    assert r.active_task["intent"] == "AFTERSALE.REFUND"
    assert r.active_task["collected_slots"] == {}


def test_unknown_junk_number_not_polluting():
    """「我有12345678个问题」：通用正则把数字串误抽成 order_id，
    但严格校验型槽位在 UNKNOWN 轮一律不算续接证据（合法应答早被
    补槽守护接走了）→ 不并入、二选一澄清。"""
    r = _resolve(
        _COLLECTING, _task(), _unknown(), slots={"order_id": "12345678", "quantity": 12345678}
    )
    assert r.unknown_with_task is True
    assert r.active_task["collected_slots"] == {}


def test_unknown_in_confirming_no_pollution_and_reconfirms():
    """CONFIRMING 下 UNKNOWN：重发确认（现行语义），且误抽值不覆盖已确认信息。"""
    task = _task(collected={"order_id": "A1"})
    r = _resolve(_CONFIRMING, task, _unknown(), slots={"order_id": "B999999999"})
    assert r.status == TurnStatus.NEEDS_CONFIRM
    # 槽位已齐（order_id 不缺）→ 本轮误抽的 B999999999 不得覆盖 A1
    assert r.active_task["collected_slots"]["order_id"] == "A1"
    assert r.unknown_with_task is False


def test_unknown_without_task_falls_back_unchanged():
    """无任务时 UNKNOWN 兜底行为不变（零回归）。"""
    r = _resolve(DialogStateValue.IDLE, None, _unknown())
    assert r.status == TurnStatus.FALLBACK
    assert r.unknown_with_task is False


# ---------------- 既有语义零回归 ----------------


def test_confirm_gate_unchanged():
    task = _task(collected={"order_id": "A1"})
    confirm = _intent(IntentLabel.META_CONFIRM, 0.9, DecisionSource.RULE_CONFIRM_GATE)
    r = _resolve(_CONFIRMING, task, confirm)
    assert r.status == TurnStatus.CONFIRMED
    assert r.finished_task is not None


def test_task_deny_unchanged():
    deny = _intent(IntentLabel.META_DENY, 0.9, DecisionSource.RULE_TASK_DENY)
    r = _resolve(_COLLECTING, _task(), deny)
    assert r.status == TurnStatus.ABORTED
    assert r.denied_task == {"intent": "AFTERSALE.REFUND"}


def test_slot_only_continuation_unchanged():
    slot_only = _intent(IntentLabel.META_SLOT_ONLY, 0.8, DecisionSource.RULE_SLOT_ONLY)
    r = _resolve(_COLLECTING, _task(), slot_only, slots={"order_id": "A12345678"})
    assert r.status == TurnStatus.NEEDS_CONFIRM
    assert r.active_task["collected_slots"]["order_id"] == "A12345678"


def test_suspended_task_resumes_after_guarded_task_finishes():
    """守护不影响任务栈恢复：切换成功后旧任务照常挂起-恢复。"""
    r = _resolve(
        _COLLECTING,
        _task(),
        _intent("LOGISTICS.TRACK", 0.95, margin=0.60),
        slots={"order_id": "A12345678"},
    )
    # 高置信切换：物流读任务槽位齐直接 DONE，退款任务从栈恢复
    assert r.status == TurnStatus.DONE
    assert r.resumed_task is not None
    assert r.resumed_task["intent"] == "AFTERSALE.REFUND"
