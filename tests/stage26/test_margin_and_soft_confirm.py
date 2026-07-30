"""Stage 26 margin 路由（P2）与软确认 quirk 修复（P4）测试。

margin 只影响「是否二判」路由，绝不改选 top2；软确认修复覆盖
写意图死区（采纳线==软确认线）与 LLM 固定置信 0.7 死条件。
monkeypatch SetFit 模型，无真实权重/LLM 依赖（无 Key 环境二判自动不可用）。
"""

import pytest

from app.chat.graph.nodes.response_generate import response_generate
from app.chat.intent.hybrid_classifier import hybrid_intent_classifier
from app.chat.intent.types import DecisionSource
from app.chat.state.types import TurnStatus
from app.core.config import settings


class _FakeModel:
    """可控的假 SetFit 模型（带 top_k 竞争者，供 margin 判定）。"""

    available = True

    def __init__(self, top: list[tuple[str, float]]):
        self._top = top

    def predict(self, text: str, top_k: int = 3):
        top = [{"label": label, "score": score} for label, score in self._top]
        return top[0]["label"], top[0]["score"], top


@pytest.fixture
def _patch_model(monkeypatch):
    def apply(top):
        monkeypatch.setattr(
            "app.chat.intent.hybrid_classifier.setfit_intent_model", _FakeModel(top)
        )

    return apply


# ---------------- margin 路由 ----------------


async def test_high_conf_large_margin_accepted(_patch_model):
    _patch_model([("LOGISTICS.TRACK", 0.78), ("ORDER.QUERY_STATUS", 0.12)])
    r = await hybrid_intent_classifier.classify("东西到哪了")
    assert r.pred_label == "LOGISTICS.TRACK"
    assert r.decision_source == DecisionSource.SETFIT
    assert r.margin == pytest.approx(0.66)


async def test_high_conf_small_margin_low_margin_source(_patch_model):
    """高分但分差小、二判不可用（无 Key）→ 采纳 top1 打 SETFIT_LOW_MARGIN。"""
    _patch_model([("LOGISTICS.TRACK", 0.78), ("ORDER.QUERY_STATUS", 0.76)])
    r = await hybrid_intent_classifier.classify("这个订单的东西什么情况")
    # 绝不改选 top2
    assert r.pred_label == "LOGISTICS.TRACK"
    assert r.decision_source == DecisionSource.SETFIT_LOW_MARGIN
    assert r.margin == pytest.approx(0.02)


async def test_low_conf_path_unchanged(_patch_model):
    """低置信路径行为不变（零回归），margin 一并落证据。"""
    _patch_model([("PRODUCT.ASK_PRICE", 0.30), ("PRODUCT.ASK_STOCK", 0.28)])
    r = await hybrid_intent_classifier.classify("嗯嗯那个啥来着")
    assert r.pred_label == "META.UNKNOWN"
    assert r.decision_source == DecisionSource.SETFIT_LOW_CONF
    assert r.margin == pytest.approx(0.02)


async def test_single_candidate_margin_is_one(_patch_model):
    _patch_model([("LOGISTICS.TRACK", 0.90)])
    r = await hybrid_intent_classifier.classify("快递到哪了")
    assert r.decision_source == DecisionSource.SETFIT
    assert r.margin == 1.0


async def test_margin_recorded_in_to_dict(_patch_model):
    _patch_model([("LOGISTICS.TRACK", 0.78), ("ORDER.QUERY_STATUS", 0.76)])
    r = await hybrid_intent_classifier.classify("这个订单的东西什么情况")
    d = r.to_dict()
    assert d["margin"] == pytest.approx(0.02)


# ---------------- 软确认 quirk 修复 ----------------


def _state(intent, conf, source, kind_hint_intent=None):
    return {
        "status": TurnStatus.NEEDS_SLOT,
        "active_task": {"intent": intent, "collected_slots": {}},
        "missing_slot": "order_id",
        "intent_result": {
            "pred_label": intent,
            "final_intent": kind_hint_intent or intent,
            "confidence": conf,
            "decision_source": source,
        },
    }


async def test_write_intent_soft_confirm_dead_zone_fixed():
    """写意图 0.60-0.75 区间新开任务 → 有软确认前缀（此前死区）。"""
    result = await response_generate(_state("AFTERSALE.REFUND", 0.65, DecisionSource.SETFIT))
    assert "对吗" in result["reply"]


async def test_write_intent_above_write_line_no_prefix():
    result = await response_generate(_state("AFTERSALE.REFUND", 0.80, DecisionSource.SETFIT))
    assert "对吗" not in result["reply"]


async def test_read_intent_line_unchanged():
    """读意图沿用 0.60 线（零回归）：0.65 不加前缀、0.45 加前缀。"""
    high = await response_generate(_state("LOGISTICS.TRACK", 0.65, DecisionSource.SETFIT))
    assert "对吗" not in high["reply"]
    low = await response_generate(_state("LOGISTICS.TRACK", 0.45, DecisionSource.SETFIT))
    assert "对吗" in low["reply"]


async def test_llm_source_always_soft_confirms():
    """LLM 二判来源（固定置信 0.7）→ 一律复述（修复死条件）。"""
    result = await response_generate(_state("LOGISTICS.TRACK", 0.7, DecisionSource.LLM))
    assert "对吗" in result["reply"]


async def test_low_margin_source_soft_confirms():
    result = await response_generate(
        _state("LOGISTICS.TRACK", 0.85, DecisionSource.SETFIT_LOW_MARGIN)
    )
    assert "对吗" in result["reply"]


# ---------------- 二选一澄清话术渲染 ----------------


async def test_switch_guard_reply():
    state = _state("AFTERSALE.REFUND", 0.9, DecisionSource.RULE_KEYWORD)
    state["switch_candidate"] = "LOGISTICS.TRACK"
    result = await response_generate(state)
    assert result["graph_trace"] == ["response_generate:switch_guard"]
    # 当前任务名与候选新意图名都在话术里
    assert "退款" in result["reply"]
    assert "物流" in result["reply"] or "查询" in result["reply"]


async def test_switch_guard_confirming_reply():
    state = _state("AFTERSALE.REFUND", 0.9, DecisionSource.RULE_KEYWORD)
    state["status"] = TurnStatus.NEEDS_CONFIRM
    state["switch_candidate"] = "LOGISTICS.TRACK"
    result = await response_generate(state)
    assert result["graph_trace"] == ["response_generate:switch_guard"]
    assert "确认" in result["reply"]


async def test_unknown_hold_reply():
    state = _state("AFTERSALE.REFUND", 0.3, DecisionSource.SETFIT_LOW_CONF)
    state["unknown_with_task"] = True
    result = await response_generate(state)
    assert result["graph_trace"] == ["response_generate:unknown_hold"]
    assert "继续办理" in result["reply"]


async def test_settings_defaults():
    """阈值默认值与文档 4.5 一致（待真实流量标定）。"""
    assert settings.INTENT_MIN_MARGIN == 0.10
    assert settings.INTENT_SWITCH_THRESHOLD_COLLECTING == 0.78
    assert settings.INTENT_SWITCH_THRESHOLD_CONFIRMING == 0.85
    assert settings.INTENT_SOFT_CONFIRM_THRESHOLD_WRITE == 0.75
