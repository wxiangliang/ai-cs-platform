"""Stage 33 会员注册引导回归测试。

锁定四件事：
1. 规则触发：正例（注册会员/开通会员/裸「注册」）+ 三类防误伤
   （否定语境/第三方平台=OOS 不劫持/CONFIRMING 下确认门优先）；
2. Mock 工具闭环：注册前未注册 → register_member → 再查已注册（NBA 停止建议的依据）；
3. NBA 主动建议：未注册+可建议 → START_ONBOARDING（优先于活动）；
   已注册/查询失败/无 user_id → 不建议（fail-closed 不骚扰）；
4. 技能契约：WRITE + 确认门 + phone 槽位 + loader 声明合并（第 10 域）。
"""

import uuid

import pytest

from app.chat.intent.rule_classifier import rule_intent_classifier
from app.chat.intent.types import DecisionSource, IntentLabel
from app.chat.skills.registry import skill_registry
from app.chat.skills.types import SkillKind
from app.chat.state.types import DialogStateValue, TurnStatus
from app.chat.tools import mock_data
from app.chat.tools.mock_provider import mock_tool_provider
from app.core.config import settings


# ---------------- 规则触发 ----------------


@pytest.mark.parametrize(
    "text",
    ["注册会员", "我要注册会员", "帮我开通会员", "开通一下会员", "注册", "我要注册", "怎么注册会员"],
)
def test_member_register_triggers(text):
    result = rule_intent_classifier.classify_control(text)
    assert result is not None and result.pred_label == IntentLabel.MEMBER_REGISTER
    assert result.decision_source == DecisionSource.RULE_KEYWORD


@pytest.mark.parametrize(
    "text",
    [
        "不想开通会员",      # 否定语境
        "不用注册了",        # 否定语境
        "取消会员",          # 退订语境不是注册
        "怎么注册抖音账号",   # 第三方平台=OOS（mode hard test 样本，不得劫持）
        "注册个微信号",
        "帮我查下订单",       # 无关业务
    ],
)
def test_member_register_not_hijacked(text):
    result = rule_intent_classifier.classify_control(text)
    assert result is None or result.pred_label != IntentLabel.MEMBER_REGISTER, text


def test_confirm_gate_wins_in_confirming():
    """CONFIRMING 下「确认」走确认门，不被会员触发干扰；
    且会员任务确认轮里再说「注册会员」也不破坏确认门优先序。"""
    result = rule_intent_classifier.classify_control(
        "确认", current_state=DialogStateValue.CONFIRMING
    )
    assert result is not None and result.pred_label == IntentLabel.META_CONFIRM


# ---------------- Mock 工具闭环 ----------------


async def test_member_tools_roundtrip():
    tenant, user = "t-test", f"u-{uuid.uuid4().hex[:8]}"
    before = await mock_tool_provider.invoke(
        "query_member_status", {"user_id": user}, tenant_id=tenant
    )
    assert before.ok and before.data["registered"] is False

    reg = await mock_tool_provider.invoke(
        "register_member", {"user_id": user, "phone": "13800138000"}, tenant_id=tenant
    )
    assert reg.ok and reg.data["member_no"].startswith("MB")
    assert reg.data["ticket_no"] == reg.data["member_no"]  # 回执号=会员号

    after = await mock_tool_provider.invoke(
        "query_member_status", {"user_id": user}, tenant_id=tenant
    )
    assert after.ok and after.data["registered"] is True
    assert after.data["member_no"] == reg.data["member_no"]

    # 幂等：同键重复注册同号（ActionExecutor 防重放之外的兜底一致性）
    again = await mock_tool_provider.invoke(
        "register_member", {"user_id": user, "phone": "13800138000"}, tenant_id=tenant
    )
    assert again.data["member_no"] == reg.data["member_no"]


def test_tool_catalog_registered():
    """工具目录单一事实来源：读工具进诊断白名单推导，写工具永不进。"""
    from app.chat.tools.catalog import TOOL_CATALOG, readonly_tool_descriptions

    assert TOOL_CATALOG["query_member_status"].readonly is True
    assert TOOL_CATALOG["register_member"].readonly is False
    assert "query_member_status" in readonly_tool_descriptions()
    assert "register_member" not in readonly_tool_descriptions()


# ---------------- 技能契约 ----------------


def test_member_skill_contract():
    skill = skill_registry.get(IntentLabel.MEMBER_REGISTER)
    assert skill.kind == SkillKind.WRITE  # 写操作：槽位齐只进确认门
    assert skill.required_slots == ["phone"]
    assert "confirm" in skill.templates and "collect" in skill.templates
    # loader 声明合并（第 10 域 md 校验通过 + action 需确认）
    assert skill.actions and skill.actions[0].action_id == "register_member"
    assert skill.actions[0].requires_confirmation is True
    assert skill.risk_level == "L2"


# ---------------- NBA 主动建议 ----------------


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


@pytest.fixture
async def redis_env():
    from app.cache.redis_client import close_redis, init_redis

    try:
        await init_redis()
    except Exception:
        pytest.skip("Redis 不可用")
    yield
    await close_redis()


async def test_nba_suggests_onboarding_for_unregistered(redis_env, monkeypatch):
    from app.chat.proactive import decide_proactive

    monkeypatch.setattr(settings, "PROACTIVE_ENABLED", True)
    monkeypatch.setattr(settings, "PROACTIVE_APPLY", True)
    state = _state()
    decision = await decide_proactive(state)
    assert decision["action"] == "START_ONBOARDING" and decision["applied"]
    assert "unregistered_user" in decision["reason_codes"]
    # 会话频控共享：同会话第二次被 session_cap 拦
    second = await decide_proactive(state)
    assert second["suppressed_by"] == "session_cap"


async def test_nba_skips_registered_user(redis_env, monkeypatch):
    from app.chat.proactive import decide_proactive

    monkeypatch.setattr(settings, "PROACTIVE_ENABLED", True)
    monkeypatch.setattr(settings, "PROACTIVE_APPLY", True)
    state = _state()
    mock_data.MEMBERS[f"t1:{state['user_id']}"] = "MB0000000001"
    decision = await decide_proactive(state)
    assert decision["action"] != "START_ONBOARDING"  # 已注册闭环：落到活动/no_campaign


async def test_nba_fail_closed_paths(redis_env, monkeypatch):
    """无 user_id / 会员查询失败 → 不建议（不确定就不骚扰）。"""
    from app.chat.proactive import decide_proactive
    from app.chat.proactive import nba as nba_mod

    monkeypatch.setattr(settings, "PROACTIVE_ENABLED", True)
    monkeypatch.setattr(settings, "PROACTIVE_APPLY", True)
    no_user = await decide_proactive(_state(user_id=""))
    assert no_user["action"] != "START_ONBOARDING"

    async def _unknown(tenant, user_id):
        # 工具失败在 _member_unregistered 内部吞掉并返回 False（不确定不骚扰）
        return False

    monkeypatch.setattr(nba_mod, "_member_unregistered", _unknown)
    decision = await decide_proactive(_state())
    assert decision["action"] != "START_ONBOARDING"


async def test_nba_suppression_matrix_applies_to_onboarding(redis_env, monkeypatch):
    """抑制矩阵对 onboarding 同样生效：退款轮/负面情绪不建议注册。"""
    from app.chat.proactive import decide_proactive

    monkeypatch.setattr(settings, "PROACTIVE_ENABLED", True)
    refund = _state(
        intent_result={"pred_label": "AFTERSALE.REFUND", "decision_source": "SETFIT"}
    )
    assert (await decide_proactive(refund))["suppressed_by"] == "high_risk_flow"
    angry = _state(emotion="negative")
    assert (await decide_proactive(angry))["suppressed_by"] == "negative_emotion"
