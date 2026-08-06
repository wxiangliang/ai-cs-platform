"""Stage 39 预约与资源调度回归测试。

锁定五件事：
1. 规则触发：BOOK/CANCEL 正例、CANCEL 先判、被动式/否定不触发、
   不劫持既有取消订单语义；
2. 槽位抽取：service_type 词表/时间表达/预约号；一次性表达同轮抽满；
3. Mock 资源池红线：容量 3 满员拒绝（SLOT_FULL）、同键幂等同号、
   过期时间拒绝、取消释放槽位、号不存在如实报错；
4. 技能契约：WRITE+确认门+槽位+loader 合并（第 11 域）；
5. 事件提醒模板注册（提醒频控/退订继承 Stage 36）。
"""

import uuid

import pytest

from app.chat.intent.rule_classifier import rule_intent_classifier
from app.chat.intent.types import DecisionSource, IntentLabel
from app.chat.skills.registry import skill_registry
from app.chat.skills.types import SkillKind
from app.chat.slots.extractor import slot_extractor
from app.chat.tools.mock_provider import mock_tool_provider
from app.services.event_service import EVENT_RULES
from app.services.journey_service import derive_transition
from app.chat.state.types import TurnStatus


# ---------------- 规则触发 ----------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("预约安装", IntentLabel.APPOINTMENT_BOOK),
        ("帮我约个维修师傅", IntentLabel.APPOINTMENT_BOOK),
        ("我要预约上门取件", IntentLabel.APPOINTMENT_BOOK),
        ("退货上门怎么预约", IntentLabel.APPOINTMENT_BOOK),
        ("预约", IntentLabel.APPOINTMENT_BOOK),  # 裸短句（收集服务类型）
        ("取消预约", IntentLabel.APPOINTMENT_CANCEL),
        ("帮我把预约取消了", IntentLabel.APPOINTMENT_CANCEL),
    ],
)
def test_appointment_triggers(text, expected):
    result = rule_intent_classifier.classify_control(text)
    assert result is not None and result.pred_label == expected, text
    assert result.decision_source == DecisionSource.RULE_KEYWORD


@pytest.mark.parametrize(
    "text",
    [
        "预约被取消了怎么回事",  # 被动式=状态咨询
        "我的预约已取消了吗",
        "取消订单",              # 既有语义不受影响
        "怎么取消自动续费",       # 既有放行样例
    ],
)
def test_appointment_not_hijacked(text):
    result = rule_intent_classifier.classify_control(text)
    assert result is None or result.pred_label not in (
        IntentLabel.APPOINTMENT_BOOK, IntentLabel.APPOINTMENT_CANCEL,
    ), (text, result and result.pred_label)


def test_cancel_order_still_works():
    result = rule_intent_classifier.classify_control("取消订单")
    assert result is not None and result.pred_label == IntentLabel.ORDER_CANCEL


# ---------------- 槽位抽取 ----------------


def test_one_shot_booking_slots():
    slots = slot_extractor.extract("预约明天下午的空调安装，电话13800138000")
    assert slots["service_type"] == "安装"
    assert "明天下午" in slots["appointment_time"]
    assert slots["phone"] == "13800138000"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("明天下午", "明天下午"),
        ("8月10日 14:00", "8月10日 14:00"),
        ("周五上午", "周五上午"),
        ("下午3点", "下午3点"),
    ],
)
def test_appointment_time_patterns(text, expected):
    assert slot_extractor.extract(text).get("appointment_time") == expected


def test_appointment_no_extraction():
    slots = slot_extractor.extract("取消预约 AP1234ABCD56")
    assert slots["appointment_no"] == "AP1234ABCD56"
    assert slot_extractor.extract("订单号 SO12345678").get("appointment_no") is None


def test_service_type_longest_first():
    assert slot_extractor.extract("预约退货上门")["service_type"] == "退货上门"


# ---------------- Mock 资源池 ----------------


async def test_capacity_idempotency_and_expiry():
    tenant = f"t-{uuid.uuid4().hex[:6]}"
    time_text = "明天上午"

    # 幂等：同 phone+类型+时间 → 同预约号
    r1 = await mock_tool_provider.invoke(
        "create_appointment",
        {"service_type": "安装", "appointment_time": time_text, "phone": "13800000001"},
        tenant_id=tenant,
    )
    assert r1.ok and r1.data["appointment_no"].startswith("AP")
    assert "北京时间" in r1.data["appointment_time"]  # 时区明示
    r1b = await mock_tool_provider.invoke(
        "create_appointment",
        {"service_type": "安装", "appointment_time": time_text, "phone": "13800000001"},
        tenant_id=tenant,
    )
    assert r1b.data["appointment_no"] == r1.data["appointment_no"]
    assert r1b.data.get("idempotent") is True

    # 容量 3：不同用户占满后第 4 个拒绝
    for i in (2, 3):
        ok = await mock_tool_provider.invoke(
            "create_appointment",
            {"service_type": "安装", "appointment_time": time_text,
             "phone": f"1380000000{i}"},
            tenant_id=tenant,
        )
        assert ok.ok
    full = await mock_tool_provider.invoke(
        "create_appointment",
        {"service_type": "安装", "appointment_time": time_text, "phone": "13800000004"},
        tenant_id=tenant,
    )
    assert full.ok is False and full.error_code == "SLOT_FULL"

    # 取消释放槽位后可再约
    cancel = await mock_tool_provider.invoke(
        "cancel_appointment", {"appointment_no": r1.data["appointment_no"]},
        tenant_id=tenant,
    )
    assert cancel.ok and cancel.data["cancelled"] is True
    retry = await mock_tool_provider.invoke(
        "create_appointment",
        {"service_type": "安装", "appointment_time": time_text, "phone": "13800000004"},
        tenant_id=tenant,
    )
    assert retry.ok

    # 过期时间拒绝 / 号不存在如实报错
    expired = await mock_tool_provider.invoke(
        "create_appointment",
        {"service_type": "维修", "appointment_time": "昨天下午", "phone": "13800000005"},
        tenant_id=tenant,
    )
    assert expired.error_code == "SLOT_EXPIRED"
    missing = await mock_tool_provider.invoke(
        "cancel_appointment", {"appointment_no": "AP0000000000"}, tenant_id=tenant
    )
    assert missing.error_code == "APPOINTMENT_NOT_FOUND"


async def test_query_slots_readonly():
    r = await mock_tool_provider.invoke(
        "query_appointment_slots", {"service_type": "安装"}, tenant_id="t1"
    )
    assert r.ok and len(r.data["available_slots"]) == 3
    assert "北京时间" in r.data["timezone"]


# ---------------- 契约 ----------------


def test_appointment_skill_contract():
    book = skill_registry.get(IntentLabel.APPOINTMENT_BOOK)
    assert book.kind == SkillKind.WRITE
    assert book.required_slots == ["service_type", "appointment_time", "phone"]
    assert book.actions and book.actions[0].action_id == "create_appointment"
    assert book.actions[0].requires_confirmation is True
    cancel = skill_registry.get(IntentLabel.APPOINTMENT_CANCEL)
    assert cancel.kind == SkillKind.WRITE
    assert cancel.actions[0].action_id == "cancel_appointment"


def test_tool_catalog_and_whitelist():
    from app.chat.tools.catalog import TOOL_CATALOG, readonly_tool_descriptions

    assert TOOL_CATALOG["query_appointment_slots"].readonly is True
    assert TOOL_CATALOG["create_appointment"].readonly is False
    assert "query_appointment_slots" in readonly_tool_descriptions()
    assert "create_appointment" not in readonly_tool_descriptions()


def test_reminder_events_registered():
    assert "APPOINTMENT_REMINDER" in EVENT_RULES
    assert "APPOINTMENT_MISSED" in EVENT_RULES


def test_journey_strong_evidence():
    t = derive_transition("NEW", "APPOINTMENT.BOOK", TurnStatus.DONE)
    assert t["stage"] == "PURCHASED"  # 约安装=已购（强证据跳阶）
