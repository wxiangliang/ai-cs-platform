"""Stage 30 Conversation Mode Gate 回归测试。

锁定四件事：
1. 接受判定：组合判据（分数/margin/业务反证/任务态加严）与红线
   （业务信号优先于闲聊信号）；
2. fail-open：产物缺失即停用、默认关零回归；
3. 双轴纪律：MODE_SOCIAL 不进 Meta 影子部署域；
4. 证据落库：IntentResult.to_dict 携带 mode_gate。
"""

import pytest

from app.chat.intent import meta_shadow
from app.chat.intent.types import DecisionSource, IntentResult
from app.chat.mode.gate import (
    MODE_OOS,
    MODE_SOCIAL_ONLY,
    ModeGate,
    business_counter_evidence,
    evaluate_oos,
    evaluate_social,
)
from app.core.config import settings


def _mode(mode=MODE_SOCIAL_ONLY, score=0.95, margin=0.5) -> dict:
    return {"mode": mode, "score": score, "margin": margin, "top": []}


# ---------------- 接受判定 ----------------


def test_social_accept_high_confidence():
    accepted, codes = evaluate_social(_mode(), "你回复得还挺快哈哈", False)
    assert accepted
    assert "social_high_confidence" in codes


def test_social_reject_business_keyword():
    """红线：业务信号优先于闲聊信号——含「退款」的吐槽不许闲聊直通。"""
    accepted, codes = evaluate_social(_mode(), "你们退款真的慢死了哈哈", False)
    assert not accepted
    assert "business_keyword" in codes


def test_social_reject_slot_value_shape():
    accepted, codes = evaluate_social(_mode(), "哈哈好的 13800138000", False)
    assert not accepted
    assert "slot_value_shape" in codes


def test_social_reject_transition_signal():
    accepted, codes = evaluate_social(_mode(), "你真有意思，顺便帮我看看那个", False)
    assert not accepted
    assert "transition_signal" in codes


def test_social_reject_low_score_and_margin():
    accepted, codes = evaluate_social(_mode(score=0.5, margin=0.05), "今天真热", False)
    assert not accepted
    assert "low_score" in codes and "low_margin" in codes


def test_social_active_task_stricter():
    """任务进行中用更高分数线：0.90 空闲期够、任务中不够。"""
    borderline = _mode(score=(settings.MODE_GATE_SOCIAL_MIN_SCORE + 0.01))
    assert evaluate_social(borderline, "哈哈你真逗", False)[0]
    accepted, codes = evaluate_social(borderline, "哈哈你真逗", True)
    assert not accepted and "low_score" in codes
    high = _mode(score=(settings.MODE_GATE_SOCIAL_MIN_SCORE_ACTIVE + 0.01))
    accepted, codes = evaluate_social(high, "哈哈你真逗", True)
    assert accepted and "social_hold_active_task" in codes


def test_non_social_mode_never_accepted():
    for mode in ("TASK_ONLY", "MIXED", "OOS", "UNCERTAIN"):
        assert not evaluate_social(_mode(mode=mode), "随便说点什么", False)[0]


def test_business_counter_evidence_clean_social():
    assert business_counter_evidence("今天天气不错呀") == []


# ---------------- OOS 边界回复（子开关默认关） ----------------


def test_oos_disabled_by_default():
    assert settings.MODE_GATE_OOS_REPLY_ENABLED is False
    assert not evaluate_oos(_mode(mode=MODE_OOS, score=0.99))


def test_oos_enabled_requires_high_confidence(monkeypatch):
    monkeypatch.setattr(settings, "MODE_GATE_OOS_REPLY_ENABLED", True)
    assert evaluate_oos(_mode(mode=MODE_OOS, score=0.95, margin=0.5))
    assert not evaluate_oos(_mode(mode=MODE_OOS, score=0.5))
    assert not evaluate_oos(_mode(mode=MODE_SOCIAL_ONLY, score=0.99))
    assert not evaluate_oos(None)


# ---------------- fail-open 与默认关 ----------------


def test_gate_disabled_by_default():
    assert settings.MODE_GATE_ENABLED is False


def test_gate_fail_open_when_artifacts_missing(tmp_path):
    gate = ModeGate(model_dir=str(tmp_path / "nonexistent"))
    assert not gate.available
    assert gate.predict([0.0] * 8) is None


# ---------------- 双轴纪律与证据落库 ----------------


def test_mode_social_not_in_meta_shadow_deploy_domain():
    """MODE_SOCIAL 不进 Meta 部署域：闲聊在模式轴不在任务操作轴，
    Meta-classifier 不加 CHITCHAT 类（stage-30 需求第 2 节，锁定）。"""
    assert DecisionSource.MODE_SOCIAL not in meta_shadow._SHADOW_SOURCES


def test_intent_result_to_dict_carries_mode_gate():
    evidence = {"mode": MODE_SOCIAL_ONLY, "score": 0.95, "accepted": True}
    result = IntentResult(
        pred_label="CHITCHAT.GENERAL",
        confidence=0.95,
        decision_source=DecisionSource.MODE_SOCIAL,
        mode_gate=evidence,
    )
    assert result.to_dict()["mode_gate"] == evidence
    # 无证据时不膨胀日志结构（与 margin/pending_fill 同约定）
    bare = IntentResult(pred_label="X", confidence=1.0, decision_source="SETFIT")
    assert "mode_gate" not in bare.to_dict()


# ---------------- 集成（需 SetFit + mode gate 产物，缺失自动跳过） ----------------

_SETFIT = __import__("pathlib").Path("models/intent_setfit_v1")
_GATE = __import__("pathlib").Path("models/mode_gate_v1")


@pytest.mark.skipif(
    not (_SETFIT.exists() and (_GATE / "mode_head.joblib").exists()),
    reason="需 SetFit 与 mode gate 训练产物（scripts/train_mode_gate.py）",
)
async def test_hybrid_social_bypass_live(monkeypatch):
    """开门后：高置信纯闲聊直通 MODE_SOCIAL；业务句正常走 SetFit。"""
    from app.chat.intent.hybrid_classifier import hybrid_intent_classifier
    from app.chat.mode.gate import mode_gate

    mode_gate.reset_for_test()
    monkeypatch.setattr(settings, "MODE_GATE_ENABLED", True)

    social = await hybrid_intent_classifier.classify("哈哈你说话真有意思")
    # 高置信时直通；模型置信不足时也必须带影子证据（不接管但可观测）
    assert social.mode_gate is not None
    if social.decision_source == DecisionSource.MODE_SOCIAL:
        assert social.pred_label == "CHITCHAT.GENERAL"
        assert social.mode_gate["accepted"] is True

    business = await hybrid_intent_classifier.classify("我要申请退款")
    assert business.decision_source != DecisionSource.MODE_SOCIAL
    assert business.pred_label != "CHITCHAT.GENERAL"
