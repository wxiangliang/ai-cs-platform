"""Stage 41 会话主动引导与建议闭环回归测试。

锁定五件事（需求第 7 节验收标准）：
1. 默认关闭零回归（welcome 默认关、followup 随 PROACTIVE 双开关）；
2. 开场引导：24h 频控 / opt-out / at_risk / Redis 故障全部不发（fail-closed）、
   旅程阶段变体门控、metadata 发送依据可追溯；
3. 接受通道：纯接受判定（残差/问候语防误伤）、窗口唯一凭据（无展示不劫持）、
   一次性消费、CONFIRMING/COLLECTING 顺序红线（确认门/补槽守护优先）；
4. 服务延伸：配置校验（accept_intent 未注册跳过）、意图前缀匹配、
   候选优先级阶梯（onboarding > followup > campaign）、抑制矩阵照常生效；
5. 决策证据：接受轮 intent_result 带 source=RULE_PROACTIVE_ACCEPT + payload 摘要。
"""

import json
import uuid
from types import SimpleNamespace

import pytest

from app.chat.intent.types import DecisionSource
from app.chat.proactive import decide_proactive
from app.chat.proactive.accept import is_pure_accept, pop_offer_accept
from app.chat.proactive.followups import load_followups, select_followup
from app.chat.proactive.welcome import load_welcome_configs, select_welcome, send_welcome_if_eligible
from app.chat.state.types import DialogStateValue, TurnStatus
from app.core.config import settings


def _state(**over):
    base = {
        "tenant_id": "t1",
        "session_id": f"s-{uuid.uuid4().hex[:8]}",
        "user_id": f"u-{uuid.uuid4().hex[:8]}",
        "status": TurnStatus.DONE,
        "intent_result": {"pred_label": "ORDER.QUERY_STATUS", "decision_source": "SETFIT"},
        "normalized_text": "帮我查下订单",
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


def _write_followups(tmp_path, monkeypatch, entries=None):
    cfg = tmp_path / "followups.json"
    cfg.write_text(json.dumps(entries if entries is not None else [
        {"followup_id": "order_to_logistics", "enabled": True,
         "trigger_intents": ["ORDER.QUERY_STATUS"],
         "suggest_key": "proactive.followup.logistics",
         "accept_intent": "LOGISTICS.TRACK", "max_per_customer": 3},
    ], ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(settings, "FOLLOWUP_CONFIG_PATH", str(cfg))


def _write_campaigns(tmp_path, monkeypatch, intents=("ORDER.",)):
    cfg = tmp_path / "campaigns.json"
    cfg.write_text(json.dumps([
        {"campaign_id": "c1", "enabled": True,
         "eligible_intents": list(intents), "hook": "满减活动"},
    ], ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(settings, "CAMPAIGN_CONFIG_PATH", str(cfg))


async def _enable(monkeypatch, apply=True, onboarding=False):
    monkeypatch.setattr(settings, "PROACTIVE_ENABLED", True)
    monkeypatch.setattr(settings, "PROACTIVE_APPLY", apply)
    monkeypatch.setattr(settings, "PROACTIVE_ONBOARDING_ENABLED", onboarding)


# ---------------- 默认关闭零回归 ----------------


def test_welcome_disabled_by_default():
    assert settings.PROACTIVE_WELCOME_ENABLED is False


async def test_welcome_noop_when_disabled():
    # 默认关：不需要 db/redis，直接返回 None（零副作用）
    assert await send_welcome_if_eligible(None, "t1", "s1", "u1") is None


# ---------------- 服务延伸：配置与匹配 ----------------


def test_followup_config_validation(tmp_path, monkeypatch):
    """accept_intent 未注册技能 → 该条跳过；缺必填字段 → 跳过；损坏 → 空池。"""
    _write_followups(tmp_path, monkeypatch, [
        {"followup_id": "ok", "enabled": True, "trigger_intents": ["ORDER."],
         "suggest_key": "proactive.followup.logistics", "accept_intent": "LOGISTICS.TRACK"},
        {"followup_id": "bad_intent", "enabled": True, "trigger_intents": ["ORDER."],
         "suggest_key": "k", "accept_intent": "NO.SUCH_INTENT"},
        {"enabled": True, "trigger_intents": ["ORDER."]},
    ])
    pool = load_followups()
    assert [f["followup_id"] for f in pool] == ["ok"]

    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(settings, "FOLLOWUP_CONFIG_PATH", str(bad))
    assert load_followups() == []


def test_followup_intent_matching(tmp_path, monkeypatch):
    _write_followups(tmp_path, monkeypatch)
    assert select_followup("ORDER.QUERY_STATUS")["followup_id"] == "order_to_logistics"
    # 未声明的意图不匹配（「有延伸就推」禁区）
    assert select_followup("PRODUCT.ASK_PRICE") is None
    # 前缀匹配
    _write_followups(tmp_path, monkeypatch, [
        {"followup_id": "f2", "enabled": True, "trigger_intents": ["ORDER."],
         "suggest_key": "proactive.followup.logistics", "accept_intent": "LOGISTICS.TRACK"},
    ])
    assert select_followup("ORDER.QUERY_STATUS")["followup_id"] == "f2"


# ---------------- 服务延伸：决策路径与优先级阶梯 ----------------


async def test_followup_wins_over_campaign(redis_env, monkeypatch, tmp_path):
    """P4 服务延伸 > P5 营销活动（同轮两者都有候选时延伸胜出）。"""
    await _enable(monkeypatch)
    _write_followups(tmp_path, monkeypatch)
    _write_campaigns(tmp_path, monkeypatch)
    decision = await decide_proactive(_state())
    assert decision["action"] == "SERVICE_FOLLOWUP"
    assert decision["followup_id"] == "order_to_logistics"
    assert decision["suggest_key"] == "proactive.followup.logistics"
    assert decision["applied"] is True


async def test_onboarding_wins_over_followup(redis_env, monkeypatch, tmp_path):
    """P3 onboarding > P4 服务延伸（阶梯测试）。"""
    await _enable(monkeypatch, onboarding=True)
    _write_followups(tmp_path, monkeypatch)

    async def fake_unregistered(tenant, user_id):
        return True

    monkeypatch.setattr("app.chat.proactive.nba._member_unregistered", fake_unregistered)
    decision = await decide_proactive(_state())
    assert decision["action"] == "START_ONBOARDING"


async def test_followup_suppressed_when_not_done(monkeypatch, tmp_path):
    """抑制矩阵对服务延伸同样生效（主诉求未闭环不追加）。"""
    await _enable(monkeypatch)
    _write_followups(tmp_path, monkeypatch)
    decision = await decide_proactive(_state(status=TurnStatus.NEEDS_SLOT))
    assert decision["action"] == "NONE" and decision["suppressed_by"] == "not_done"


async def test_followup_customer_cap(redis_env, monkeypatch, tmp_path):
    """客户×延伸频控：达 max_per_customer 后回落（此处无活动池→NONE）。"""
    await _enable(monkeypatch)
    _write_followups(tmp_path, monkeypatch, [
        {"followup_id": "f1", "enabled": True, "trigger_intents": ["ORDER."],
         "suggest_key": "proactive.followup.logistics",
         "accept_intent": "LOGISTICS.TRACK", "max_per_customer": 1},
    ])
    monkeypatch.setattr(settings, "PROACTIVE_SESSION_MAX", 10)
    user = f"u-{uuid.uuid4().hex[:8]}"
    first = await decide_proactive(_state(user_id=user))
    assert first["action"] == "SERVICE_FOLLOWUP"
    second = await decide_proactive(_state(user_id=user))
    assert second["action"] == "NONE"


# ---------------- 接受通道：纯接受判定 ----------------


def test_pure_accept_matching():
    for text in ("好的", "可以", "行", "嗯嗯", "好的呢", "发我看看", "OK", "要的，麻烦了"):
        assert is_pure_accept(text), text


def test_pure_accept_rejects_residue_and_greetings():
    # 带业务残差不判接受（红线 2：照常走分类/多意图）
    for text in ("好的，另外我要退货", "可以，先帮我查下物流", "好的帮我退款"):
        assert not is_pure_accept(text), text
    # 问候/疑问/否定防误伤
    for text in ("你好", "您好", "hello", "可以吗", "不要", "不用了", ""):
        assert not is_pure_accept(text), text


# ---------------- 接受通道：窗口消费（Redis） ----------------


async def test_accept_requires_offer_window(redis_env):
    """无展示窗口时「好的」绝不被劫持（窗口是唯一凭据）。"""
    assert await pop_offer_accept("t1", f"s-{uuid.uuid4().hex[:8]}", "好的") is None


async def test_accept_consumes_window_once(redis_env):
    from app.cache.redis_client import get_redis_client

    session_id = f"s-{uuid.uuid4().hex[:8]}"
    key = f"proactive:last:t1:{session_id}"
    await get_redis_client().set(key, json.dumps(
        {"action": "SERVICE_FOLLOWUP", "id": "f1", "accept_intent": "LOGISTICS.TRACK"}
    ), ex=600)
    offer = await pop_offer_accept("t1", session_id, "好的")
    assert offer == {"action": "SERVICE_FOLLOWUP", "id": "f1", "accept_intent": "LOGISTICS.TRACK"}
    # 一次性消费：同一展示不能被接受两次
    assert await pop_offer_accept("t1", session_id, "好的") is None


async def test_accept_without_accept_intent_is_noop(redis_env):
    """活动未声明 accept_intent → 消费窗口但不开任务（走正常分类）。"""
    from app.cache.redis_client import get_redis_client

    session_id = f"s-{uuid.uuid4().hex[:8]}"
    key = f"proactive:last:t1:{session_id}"
    await get_redis_client().set(key, json.dumps({"action": "MENTION_CAMPAIGN", "id": "c1"}), ex=600)
    assert await pop_offer_accept("t1", session_id, "好的") is None


async def test_accept_redis_unavailable_degrades():
    """Redis 不可用 → 判定失败走正常分类（无害降级）。"""
    assert await pop_offer_accept("t1", "s1", "好的") is None


# ---------------- 接受通道：节点集成与顺序红线 ----------------


async def _classify(monkeypatch, text, current_state=DialogStateValue.IDLE, task=None):
    from app.chat.graph.nodes.intent_classify import intent_classify

    monkeypatch.setattr(settings, "PROACTIVE_ENABLED", True)
    monkeypatch.setattr(settings, "PROACTIVE_APPLY", True)
    state = {
        "tenant_id": "t1",
        "session_id": _classify.session_id,
        "normalized_text": text,
        "current_state": current_state,
        "active_task": task,
    }
    return await intent_classify(state)


async def _set_window(session_id, accept_intent="LOGISTICS.TRACK"):
    from app.cache.redis_client import get_redis_client

    await get_redis_client().set(
        f"proactive:last:t1:{session_id}",
        json.dumps({"action": "SERVICE_FOLLOWUP", "id": "f1", "accept_intent": accept_intent}),
        ex=600,
    )


async def test_node_accepts_offer(redis_env, monkeypatch):
    """展示→「好的」→ 按 accept_intent 开任务，决策证据齐全。"""
    _classify.session_id = f"s-{uuid.uuid4().hex[:8]}"
    await _set_window(_classify.session_id)
    result = await _classify(monkeypatch, "好的")
    intent = result["intent_result"]
    assert intent["pred_label"] == "LOGISTICS.TRACK"
    assert intent["decision_source"] == DecisionSource.RULE_PROACTIVE_ACCEPT
    assert intent["proactive_accept"]["id"] == "f1"


async def test_node_confirm_gate_wins_over_accept(redis_env, monkeypatch):
    """顺序红线：CONFIRMING 下「好的」仍归确认门（绝不被营销应答劫持）。"""
    _classify.session_id = f"s-{uuid.uuid4().hex[:8]}"
    await _set_window(_classify.session_id)
    result = await _classify(
        monkeypatch, "好的",
        current_state=DialogStateValue.CONFIRMING,
        task={"intent": "AFTERSALE.REFUND", "collected_slots": {}},
    )
    intent = result["intent_result"]
    assert intent["pred_label"] == "META.CONFIRM"
    assert intent["decision_source"] == DecisionSource.RULE_CONFIRM_GATE
    # 窗口未被消费（留给自然过期）
    from app.cache.redis_client import get_redis_client

    assert await get_redis_client().exists(f"proactive:last:t1:{_classify.session_id}")


async def test_node_collecting_not_hijacked(redis_env, monkeypatch):
    """COLLECTING（补槽中）不判接受：任务否定/补槽守护优先。"""
    _classify.session_id = f"s-{uuid.uuid4().hex[:8]}"
    await _set_window(_classify.session_id)
    result = await _classify(
        monkeypatch, "好的",
        current_state=DialogStateValue.COLLECTING,
        task={"intent": "AFTERSALE.REFUND", "collected_slots": {}, "required_slots": ["order_id"]},
    )
    assert result["intent_result"]["decision_source"] != DecisionSource.RULE_PROACTIVE_ACCEPT


async def test_node_business_residue_not_accepted(redis_env, monkeypatch):
    """「好的，另外我要退货」→ 不判接受，窗口不消费，走正常分类。"""
    _classify.session_id = f"s-{uuid.uuid4().hex[:8]}"
    await _set_window(_classify.session_id)
    result = await _classify(monkeypatch, "好的，另外我要退货")
    assert result["intent_result"]["decision_source"] != DecisionSource.RULE_PROACTIVE_ACCEPT


# ---------------- 开场引导 ----------------


def _stub_message_repo(monkeypatch, sink):
    from app.repositories.chat_message_repository import chat_message_repository

    async def fake_create(db, **kwargs):
        sink.append(kwargs)
        return SimpleNamespace(id=f"m-{len(sink)}")

    monkeypatch.setattr(chat_message_repository, "create", fake_create)


def _stub_journey(monkeypatch, stage=None, at_risk=False):
    from app.services.journey_service import journey_service

    async def fake_get_stage(db, tenant_id, user_id):
        return {"stage": stage, "at_risk": at_risk} if stage or at_risk else None

    monkeypatch.setattr(journey_service, "get_stage", fake_get_stage)


def _stub_ws(monkeypatch, events):
    from app.services import notify_service

    monkeypatch.setattr(
        notify_service.ws_hub, "publish_after_commit",
        lambda db, channel, event: events.append((channel, event)),
    )


async def _enable_welcome(monkeypatch):
    monkeypatch.setattr(settings, "PROACTIVE_ENABLED", True)
    monkeypatch.setattr(settings, "PROACTIVE_WELCOME_ENABLED", True)


async def test_welcome_sends_once_per_day(redis_env, monkeypatch):
    """发送一次（metadata 含 config_id）；同客户 24h 内第二个会话不发。"""
    await _enable_welcome(monkeypatch)
    messages, events = [], []
    _stub_message_repo(monkeypatch, messages)
    _stub_journey(monkeypatch)
    _stub_ws(monkeypatch, events)
    user = f"u-{uuid.uuid4().hex[:8]}"
    first = await send_welcome_if_eligible(object(), "t1", "s1", user)
    assert first is not None and first["welcome_id"] == "default"
    assert messages[0]["metadata_json"]["category"] == "welcome"
    assert messages[0]["metadata_json"]["config_id"] == "default"
    assert messages[0]["role"] == "assistant" and messages[0]["content"]
    assert events and events[0][1]["type"] == "proactive"
    # 24h 频控：第二个会话不发
    assert await send_welcome_if_eligible(object(), "t1", "s2", user) is None


async def test_welcome_failclosed_no_user_or_redis(monkeypatch):
    """无 user_id 不发；Redis 不可用不发（fail-closed）。"""
    await _enable_welcome(monkeypatch)
    assert await send_welcome_if_eligible(object(), "t1", "s1", "") is None
    # Redis 未初始化（无 redis_env fixture）→ 不发
    assert await send_welcome_if_eligible(object(), "t1", "s1", "u1") is None


async def test_welcome_suppressed_for_at_risk(redis_env, monkeypatch):
    """at_risk（投诉/退款/低分中）客户不做开场推介。"""
    await _enable_welcome(monkeypatch)
    messages = []
    _stub_message_repo(monkeypatch, messages)
    _stub_journey(monkeypatch, stage="PURCHASED", at_risk=True)
    assert await send_welcome_if_eligible(object(), "t1", "s1", f"u-{uuid.uuid4().hex[:8]}") is None
    assert messages == []


async def test_welcome_suppressed_for_optout(redis_env, monkeypatch):
    await _enable_welcome(monkeypatch)
    user = f"u-{uuid.uuid4().hex[:8]}"
    from app.cache.redis_client import get_redis_client

    await get_redis_client().set(f"proactive:optout:t1:{user}", "1")
    assert await send_welcome_if_eligible(object(), "t1", "s1", user) is None


async def test_welcome_journey_variant(redis_env, monkeypatch, tmp_path):
    """旅程阶段变体：PURCHASED 客户命中服务型条目；阶段未知回落 default。"""
    await _enable_welcome(monkeypatch)
    cfg = tmp_path / "welcome.json"
    cfg.write_text(json.dumps([
        {"welcome_id": "purchased_service", "enabled": True,
         "eligible_journey_stages": ["PURCHASED"], "hook_key": "proactive.welcome.purchased"},
        {"welcome_id": "default", "enabled": True, "hook_key": "proactive.welcome"},
    ], ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(settings, "WELCOME_CONFIG_PATH", str(cfg))
    messages, events = [], []
    _stub_message_repo(monkeypatch, messages)
    _stub_ws(monkeypatch, events)
    _stub_journey(monkeypatch, stage="PURCHASED")
    got = await send_welcome_if_eligible(object(), "t1", "s1", f"u-{uuid.uuid4().hex[:8]}")
    assert got["welcome_id"] == "purchased_service"
    _stub_journey(monkeypatch)  # 阶段未知
    got = await send_welcome_if_eligible(object(), "t1", "s1", f"u-{uuid.uuid4().hex[:8]}")
    assert got["welcome_id"] == "default"


def test_welcome_config_fallback_and_broken(tmp_path, monkeypatch):
    """文件缺失回退内置默认；损坏=空表（运营意图不明宁可不发）。"""
    monkeypatch.setattr(settings, "WELCOME_CONFIG_PATH", str(tmp_path / "none.json"))
    assert load_welcome_configs()[0]["welcome_id"] == "default"
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(settings, "WELCOME_CONFIG_PATH", str(bad))
    assert load_welcome_configs() == []
    assert select_welcome(None) is None
