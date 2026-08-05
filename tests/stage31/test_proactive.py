"""Stage 31 主动服务回归测试。

锁定四件事：
1. 抑制矩阵（包 priority_and_suppression 冲突示例表全覆盖）：
   退款/投诉/确认门/负面情绪/闲聊轮营销触发数必须为 0；
2. 活动资格：启用/有效期/意图相关性（显式声明，防「有活动就推」）；
3. 频控与拒绝闭环（Redis）：会话上限/客户×活动上限/拒绝冷却/强拒绝退出；
4. 默认双关零回归 + Redis 故障 fail-closed（宁可少发不能超发）。
"""

import uuid
from datetime import datetime, timezone

import pytest

from app.chat.proactive import decide_proactive
from app.chat.proactive.campaigns import campaign_eligible, load_campaigns
from app.chat.proactive.nba import suppression_reason
from app.chat.state.types import TurnStatus
from app.core.config import settings

NOW = datetime(2026, 8, 15, tzinfo=timezone.utc)


def _campaign(**over):
    base = {
        "campaign_id": "c1",
        "enabled": True,
        "valid_from": "2026-08-01T00:00:00+00:00",
        "valid_to": "2026-08-31T23:59:59+00:00",
        "eligible_intents": ["PRODUCT.", "FAQ.GENERAL"],
        "hook": "满 3000 减 200",
        "max_per_customer": 2,
    }
    return {**base, **over}


def _state(**over):
    base = {
        "tenant_id": "t1",
        "session_id": f"s-{uuid.uuid4().hex[:8]}",
        "user_id": f"u-{uuid.uuid4().hex[:8]}",
        "status": TurnStatus.DONE,
        "intent_result": {"pred_label": "PRODUCT.ASK_PRICE", "decision_source": "SETFIT"},
        "normalized_text": "这款空调多少钱",
    }
    return {**base, **over}


# ---------------- 抑制矩阵（纯函数，包冲突示例表） ----------------


def test_suppress_refund_collecting():
    """退款 COLLECTING 推配件 → 抑制（not_done 先命中，双保险 high_risk）。"""
    state = _state(
        status=TurnStatus.NEEDS_SLOT,
        intent_result={"pred_label": "AFTERSALE.REFUND", "decision_source": "SETFIT"},
    )
    assert suppression_reason(state) == "not_done"


def test_suppress_high_risk_flow_even_done():
    """投诉/退款/取消/支付即使 DONE 也禁营销（高风险流程绝对禁区）。"""
    for intent in ("AFTERSALE.COMPLAIN", "AFTERSALE.REFUND", "ORDER.CANCEL", "PAYMENT.ISSUE"):
        state = _state(intent_result={"pred_label": intent, "decision_source": "SETFIT"})
        assert suppression_reason(state) == "high_risk_flow", intent


def test_suppress_confirm_gate_turn():
    assert suppression_reason(_state(status=TurnStatus.NEEDS_CONFIRM)) == "not_done"


def test_suppress_negative_emotion():
    assert suppression_reason(_state(emotion="negative")) == "negative_emotion"


def test_suppress_social_only_turn():
    """SOCIAL_ONLY 轮不触发营销（模式门直通来源与影子证据两条路都拦）。"""
    state = _state(
        intent_result={"pred_label": "CHITCHAT.GENERAL", "decision_source": "MODE_SOCIAL"}
    )
    assert suppression_reason(state) == "social_only"
    shadow = _state(
        intent_result={
            "pred_label": "CHITCHAT.GENERAL",
            "decision_source": "SETFIT",
            "mode_gate": {"mode": "SOCIAL_ONLY", "accepted": False},
        }
    )
    assert suppression_reason(shadow) == "social_only"


def test_suppress_blocked_handoff_csat():
    assert suppression_reason(_state(blocked=True)) == "blocked"
    assert suppression_reason(_state(handoff_silent=True)) == "handoff"
    assert suppression_reason(_state(csat_capture={"score": 5})) == "csat"


def test_allow_product_price_done():
    """查询价格完成 → 允许（包示例表「任务相关」行）。"""
    assert suppression_reason(_state()) is None


# ---------------- 活动资格 ----------------


def test_campaign_eligibility_rules():
    assert campaign_eligible(_campaign(), "PRODUCT.ASK_PRICE", NOW)
    assert campaign_eligible(_campaign(), "FAQ.GENERAL", NOW)
    # 相关性：未声明的意图不推（退款域根本不在 eligible_intents）
    assert not campaign_eligible(_campaign(), "AFTERSALE.REFUND", NOW)
    assert not campaign_eligible(_campaign(enabled=False), "PRODUCT.ASK_PRICE", NOW)
    late = datetime(2026, 9, 5, tzinfo=timezone.utc)
    assert not campaign_eligible(_campaign(), "PRODUCT.ASK_PRICE", late)
    # 空 eligible_intents = 不相关（必须显式声明面向哪些意图）
    assert not campaign_eligible(_campaign(eligible_intents=[]), "PRODUCT.ASK_PRICE", NOW)


def test_config_fail_open_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "CAMPAIGN_CONFIG_PATH", str(tmp_path / "none.json"))
    assert load_campaigns() == []
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(settings, "CAMPAIGN_CONFIG_PATH", str(bad))
    assert load_campaigns() == []


# ---------------- 默认关与 fail-closed ----------------


def test_disabled_by_default():
    assert settings.PROACTIVE_ENABLED is False
    assert settings.PROACTIVE_APPLY is False


async def test_decide_returns_none_when_disabled():
    assert await decide_proactive(_state()) is None


async def test_redis_unavailable_fail_closed(monkeypatch, tmp_path):
    """Redis 未初始化时必须抑制（宁可少发不能超发）。"""
    cfg = tmp_path / "c.json"
    cfg.write_text('[{"campaign_id":"c1","enabled":true,"eligible_intents":["PRODUCT."],"hook":"x"}]', encoding="utf-8")
    monkeypatch.setattr(settings, "CAMPAIGN_CONFIG_PATH", str(cfg))
    monkeypatch.setattr(settings, "PROACTIVE_ENABLED", True)
    monkeypatch.setattr(settings, "PROACTIVE_APPLY", True)
    decision = await decide_proactive(_state())
    assert decision is not None and decision["action"] == "NONE"
    assert decision["suppressed_by"] == "redis_unavailable"


# ---------------- 频控与拒绝闭环（需 Redis，同 stage14 模式） ----------------


@pytest.fixture
async def redis_env():
    from app.cache.redis_client import close_redis, init_redis

    try:
        await init_redis()
    except Exception:
        pytest.skip("Redis 不可用")
    yield
    await close_redis()


async def _enable(monkeypatch, tmp_path, apply=True):
    cfg = tmp_path / "campaigns.json"
    cfg.write_text(
        '[{"campaign_id":"c1","enabled":true,"eligible_intents":["PRODUCT."],'
        '"hook":"满减活动","max_per_customer":2}]',
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "CAMPAIGN_CONFIG_PATH", str(cfg))
    monkeypatch.setattr(settings, "PROACTIVE_ENABLED", True)
    monkeypatch.setattr(settings, "PROACTIVE_APPLY", apply)


async def test_offer_then_session_cap(redis_env, monkeypatch, tmp_path):
    await _enable(monkeypatch, tmp_path)
    state = _state()
    first = await decide_proactive(state)
    assert first["action"] == "MENTION_CAMPAIGN" and first["applied"]
    assert first["campaign_id"] == "c1" and first["hook"]
    # 同会话第二次：会话频控拦截
    second = await decide_proactive(state)
    assert second["action"] == "NONE" and second["suppressed_by"] == "session_cap"


async def test_shadow_mode_does_not_record_impression(redis_env, monkeypatch, tmp_path):
    """影子（ENABLED 不 APPLY）：产出决策但 applied=False、不记频控。"""
    await _enable(monkeypatch, tmp_path, apply=False)
    state = _state()
    for _ in range(2):
        decision = await decide_proactive(state)
        assert decision["action"] == "MENTION_CAMPAIGN"
        assert decision["applied"] is False


async def test_rejection_cooldown_and_optout(redis_env, monkeypatch, tmp_path):
    await _enable(monkeypatch, tmp_path)
    state = _state()
    assert (await decide_proactive(state))["applied"]
    # 上轮展示过 + 本轮拒绝语 → 冷却，且本轮不展示
    reject = {**state, "normalized_text": "不用推荐了"}
    decision = await decide_proactive(reject)
    assert decision["suppressed_by"] == "rejected_cooldown"
    # 冷却期内换会话也不展示（冷却按客户）
    other = {**state, "session_id": f"s-{uuid.uuid4().hex[:8]}"}
    assert (await decide_proactive(other))["suppressed_by"] == "rejected_cooldown"


async def test_strong_rejection_opts_out(redis_env, monkeypatch, tmp_path):
    await _enable(monkeypatch, tmp_path)
    state = _state()
    assert (await decide_proactive(state))["applied"]
    strong = {**state, "normalized_text": "以后都别推这些"}
    assert (await decide_proactive(strong))["suppressed_by"] == "opted_out"
    other = {**state, "session_id": f"s-{uuid.uuid4().hex[:8]}"}
    assert (await decide_proactive(other))["suppressed_by"] == "opted_out"


async def test_rejection_without_prior_offer_ignored(redis_env, monkeypatch, tmp_path):
    """没展示过就说「不用推荐」不算对我们的拒绝（不误伤后续资格）。"""
    await _enable(monkeypatch, tmp_path)
    state = _state(normalized_text="不用推荐了")
    decision = await decide_proactive(state)
    assert decision["action"] == "MENTION_CAMPAIGN"


async def test_customer_campaign_cap(redis_env, monkeypatch, tmp_path):
    """客户×活动上限：跨会话累计 2 次后第三次抑制。"""
    await _enable(monkeypatch, tmp_path)
    user = f"u-{uuid.uuid4().hex[:8]}"
    for i in range(2):
        state = _state(user_id=user)
        assert (await decide_proactive(state))["applied"], i
    third = _state(user_id=user)
    assert (await decide_proactive(third))["suppressed_by"] == "campaign_cap"
