"""Stage 34 Case 工单与 SLA 治理回归测试（需 PostgreSQL）。

锁定五件事：
1. 开/并幂等：同客户同类型并入不重开；更高优先级触发提级并重算 SLA；
2. 生命周期白名单：非法迁移拒绝；resolve/close/reopen 副作用正确；
3. SLA 超时升级：cron 逻辑幂等（ESCALATED 不重复取）；
4. 补偿政策：命中/不命中/低优先级/重复评估 reason_codes 正确，LLM 零参与；
5. 状态机白名单表本身自洽（9 态全部可达、终态可重开）。
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import settings
from app.db.session import AsyncSessionLocal, dispose_engine
from app.models.chat_service_case import ACTIVE_CASE_STATUSES
from app.repositories.chat_service_case_repository import chat_service_case_repository
from app.services.case_service import (
    REASON_CASE_MAP,
    STATUS_TRANSITIONS,
    case_service,
    evaluate_compensation,
    sla_due,
)

TENANT = "t-case-test"


@pytest.fixture
async def db():
    try:
        async with AsyncSessionLocal() as session:
            yield session
            await session.rollback()
    finally:
        await dispose_engine()


def _user() -> str:
    return f"u-{uuid.uuid4().hex[:8]}"


# ---------------- 开/并幂等 ----------------


async def test_open_then_merge_same_type(db):
    user = _user()
    case_id, created = await case_service.open_or_merge(
        db, tenant_id=TENANT, user_id=user, reason="USER_REQUEST",
        refs={"sessions": ["s1"], "tickets": ["tk1"]},
    )
    assert created
    case = await chat_service_case_repository.get_owned(db, TENANT, case_id)
    assert case.status == "OPEN" and case.case_type == "GENERAL_SUPPORT"
    assert case.case_no.startswith("CS")
    assert case.sla_due_at is not None

    # 同类型再触发 → 并入（关联追加，不新建）
    again_id, created2 = await case_service.open_or_merge(
        db, tenant_id=TENANT, user_id=user, reason="MANUAL",
        refs={"sessions": ["s2"], "tickets": ["tk2"], "orders": ["O123"]},
    )
    assert again_id == case_id and not created2
    assert case.related_json["sessions"] == ["s1", "s2"]
    assert case.related_json["orders"] == ["O123"]


async def test_merge_escalates_priority_and_sla(db):
    """并入时新触发优先级更高 → 提级 + SLA 重算（缩短）。"""
    user = _user()
    # REPEATED_UNKNOWN 与 EXECUTION_FAILED 不同类型不并——用同类型不同优先级：
    # GENERAL_SUPPORT(NORMAL) 先开，手动改低优先级再并入验证提级路径
    case_id, _ = await case_service.open_or_merge(
        db, tenant_id=TENANT, user_id=user, reason="USER_REQUEST", refs={}
    )
    case = await chat_service_case_repository.get_owned(db, TENANT, case_id)
    case.priority = "LOW"
    old_due = case.sla_due_at
    await case_service.open_or_merge(
        db, tenant_id=TENANT, user_id=user, reason="USER_REQUEST", refs={}
    )
    # 同优先级 NORMAL > LOW → 提级并重算
    assert case.priority == "NORMAL"
    assert case.sla_due_at != old_due


async def test_different_type_opens_new_case(db):
    user = _user()
    a, _ = await case_service.open_or_merge(
        db, tenant_id=TENANT, user_id=user, reason="USER_REQUEST", refs={}
    )
    b, created = await case_service.open_or_merge(
        db, tenant_id=TENANT, user_id=user, reason="PAYMENT_ISSUE", refs={}
    )
    assert created and a != b
    payment = await chat_service_case_repository.get_owned(db, TENANT, b)
    assert payment.priority == "HIGH"  # 支付类高优先级


# ---------------- 生命周期 ----------------


async def test_lifecycle_transitions(db):
    user = _user()
    case_id, _ = await case_service.open_or_merge(
        db, tenant_id=TENANT, user_id=user, reason="USER_REQUEST", refs={}
    )
    case = await chat_service_case_repository.get_owned(db, TENANT, case_id)

    assert await case_service.transition(db, case, "IN_PROGRESS", operator="agent1")
    assert case.owner_type == "HUMAN" and case.owner_id == "agent1"

    # 非法迁移：IN_PROGRESS → REOPENED
    assert not await case_service.transition(db, case, "REOPENED")

    assert await case_service.transition(db, case, "RESOLVED", resolution_code="FIXED")
    assert case.resolution_code == "FIXED" and case.resolved_at is not None

    # RESOLVED → REOPENED：计数 + 清解决态 + SLA 重算
    assert await case_service.transition(db, case, "REOPENED")
    assert case.reopen_count == 1
    assert case.resolution_code is None and case.resolved_at is None
    assert case.sla_due_at is not None

    assert await case_service.transition(db, case, "CLOSED")
    assert case.closed_at is not None


def test_transition_table_self_consistent():
    """白名单表自洽：9 态齐全；终态可重开；活跃态都能到 RESOLVED。"""
    all_statuses = set(STATUS_TRANSITIONS)
    assert all_statuses == {
        "OPEN", "IN_PROGRESS", "WAITING_CUSTOMER", "WAITING_INTERNAL",
        "WAITING_EXTERNAL", "RESOLVED", "CLOSED", "REOPENED", "ESCALATED",
    }
    assert "REOPENED" in STATUS_TRANSITIONS["RESOLVED"]
    assert "REOPENED" in STATUS_TRANSITIONS["CLOSED"]
    for status in ACTIVE_CASE_STATUSES:
        assert "RESOLVED" in STATUS_TRANSITIONS[status], status


# ---------------- SLA ----------------


async def test_sla_escalation_idempotent(db):
    user = _user()
    case_id, _ = await case_service.open_or_merge(
        db, tenant_id=TENANT, user_id=user, reason="USER_REQUEST", refs={}
    )
    case = await chat_service_case_repository.get_owned(db, TENANT, case_id)
    case.sla_due_at = datetime.now(timezone.utc) - timedelta(hours=1)
    await db.flush()

    n = await case_service.escalate_breached(db)
    assert n >= 1
    # 同一 session 身份映射对象已被升级逻辑就地修改
    assert case.status == "ESCALATED" and case.priority == "HIGH"
    assert case.metadata_json["escalations"][0]["reason"] == "SLA_BREACH"

    # 幂等：已 ESCALATED 不重复升级
    ids = [c.id for c in await chat_service_case_repository.list_sla_breached(
        db, datetime.now(timezone.utc)
    )]
    assert case_id not in ids


def test_sla_due_by_priority():
    now = datetime.now(timezone.utc)
    assert sla_due("HIGH", now) == now + timedelta(hours=settings.CASE_SLA_HOURS_HIGH)
    assert sla_due("NORMAL", now) == now + timedelta(hours=settings.CASE_SLA_HOURS_NORMAL)
    assert sla_due("LOW", now) == now + timedelta(hours=settings.CASE_SLA_HOURS_LOW)


# ---------------- 补偿政策（Service Recovery） ----------------


def _case_stub(case_type="EXECUTION_FAILURE", priority="HIGH", status="OPEN", meta=None):
    from app.models.chat_service_case import ChatServiceCase

    c = ChatServiceCase()
    c.case_type = case_type
    c.priority = priority
    c.status = status
    c.metadata_json = meta
    return c


@pytest.fixture
def policies(monkeypatch, tmp_path):
    cfg = tmp_path / "policies.json"
    cfg.write_text(
        '[{"policy_code":"EXEC_V1","case_types":["EXECUTION_FAILURE"],'
        '"min_priority":"HIGH","compensation_type":"COUPON","max_value":100,'
        '"requires_approval":true}]',
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "COMPENSATION_POLICY_PATH", str(cfg))


def test_compensation_eligible(policies):
    result = evaluate_compensation(_case_stub())
    assert result["eligible"] and result["policy_code"] == "EXEC_V1"
    assert result["requires_approval"] is True
    assert "policy:EXEC_V1" in result["reason_codes"]


def test_compensation_rejections(policies):
    # 优先级不足
    low = evaluate_compensation(_case_stub(priority="NORMAL"))
    assert not low["eligible"] and "below_min_priority:EXEC_V1" in low["reason_codes"]
    # 类型不匹配
    other = evaluate_compensation(_case_stub(case_type="GENERAL_SUPPORT"))
    assert not other["eligible"] and "no_matching_policy" in other["reason_codes"]
    # 非活跃 Case
    closed = evaluate_compensation(_case_stub(status="CLOSED"))
    assert not closed["eligible"] and "case_not_active" in closed["reason_codes"]
    # 已评估过（快照幂等）
    dup = evaluate_compensation(_case_stub(meta={"compensation": {"eligible": True}}))
    assert not dup["eligible"] and "already_evaluated" in dup["reason_codes"]


def test_compensation_no_config(monkeypatch, tmp_path):
    """无政策配置 = 一律不符合（不猜测不放水）。"""
    monkeypatch.setattr(settings, "COMPENSATION_POLICY_PATH", str(tmp_path / "none.json"))
    result = evaluate_compensation(_case_stub())
    assert not result["eligible"]


# ---------------- 触发映射契约 ----------------


def test_reason_map_covers_handoff_reasons():
    """五类转人工触发 + CSAT 低分全部有映射（收口点契约）。"""
    for reason in ("USER_REQUEST", "PAYMENT_ISSUE", "EXECUTION_FAILED",
                   "REPEATED_UNKNOWN", "SKILL_RULE", "ABUSE", "MANUAL", "LOW_CSAT"):
        assert reason in REASON_CASE_MAP, reason
    assert REASON_CASE_MAP["PAYMENT_ISSUE"][1] == "HIGH"
    assert REASON_CASE_MAP["EXECUTION_FAILED"][1] == "HIGH"
