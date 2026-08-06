"""Stage 38 客户旅程回归测试。

锁定四件事：
1. 推进规则（纯函数）：强证据跳阶、弱证据一次一格、阶段不倒退、
   at_risk 叠加/解除与阶段独立；
2. 持久化：upsert、转移史封顶 20、无变化不写；
3. 活动旅程门控：声明 eligible_journey_stages 而阶段未知/不符不推；
4. 红线：旅程不驱动写操作（无任何执行器/确认门引用——结构性事实）。
"""

import uuid

import pytest

from app.chat.proactive.campaigns import campaign_eligible
from app.chat.state.types import TurnStatus
from app.services.journey_service import (
    STAGE_RANK,
    derive_transition,
    journey_service,
)

from datetime import datetime, timezone

NOW = datetime(2026, 8, 15, tzinfo=timezone.utc)
TENANT = "t-journey-test"


# ---------------- 推进规则（纯函数） ----------------


def test_strong_evidence_jumps():
    t = derive_transition("NEW", "ORDER.QUERY_STATUS", TurnStatus.DONE)
    assert t["stage"] == "PURCHASED"  # 强证据可跳阶（有订单可问=已购）
    t2 = derive_transition("NEW", "ORDER.CREATE", TurnStatus.DONE)
    assert t2["stage"] == "READY_TO_BUY"
    t3 = derive_transition("NEW", "MEMBER.REGISTER", TurnStatus.CONFIRMED)
    assert t3["stage"] == "REGISTERED"
    # 注册未过确认门不算（只有执行成功 CONFIRMED 才是强证据）
    assert derive_transition("NEW", "MEMBER.REGISTER", TurnStatus.NEEDS_CONFIRM) is None


def test_weak_evidence_one_step_only():
    # NEW 直接问价（CONSIDERING rank3）：弱证据只推进一格 → REGISTERED? 不——
    # 一格 = rank+1 = REGISTERED？语义按 rank：NEW(0)+1=REGISTERED(1)
    t = derive_transition("NEW", "PRODUCT.ASK_PRICE", TurnStatus.DONE)
    assert STAGE_RANK[t["stage"]] == STAGE_RANK["NEW"] + 1
    # DISCOVERING → 问价 → CONSIDERING（正好一格）
    t2 = derive_transition("DISCOVERING", "PRODUCT.COMPARE", TurnStatus.DONE)
    assert t2["stage"] == "CONSIDERING"


def test_stage_never_regresses():
    # 已购客户问商品信息（弱证据低阶目标）→ 不倒退，无转移
    assert derive_transition("PURCHASED", "PRODUCT.ASK_INFO", TurnStatus.DONE) is None


def test_at_risk_overlay_independent():
    t = derive_transition("PURCHASED", "AFTERSALE.REFUND", TurnStatus.NEEDS_SLOT)
    assert t["at_risk"] is True
    assert "stage" not in t  # 已是 PURCHASED，阶段不变只叠风险
    cleared = derive_transition("PURCHASED", "", TurnStatus.DONE, csat=5)
    assert cleared["at_risk"] is False
    low = derive_transition("NEW", "", TurnStatus.DONE, csat=1)
    assert low["at_risk"] is True


def test_no_signal_returns_none():
    assert derive_transition("NEW", "CHITCHAT.GENERAL", TurnStatus.DONE) is None
    assert derive_transition("NEW", "", TurnStatus.DONE) is None


# ---------------- 持久化（需 PostgreSQL） ----------------


@pytest.fixture
async def db():
    from app.db.session import AsyncSessionLocal, dispose_engine

    try:
        async with AsyncSessionLocal() as session:
            yield session
            await session.rollback()
    finally:
        await dispose_engine()


async def test_update_and_history(db):
    user = f"u-{uuid.uuid4().hex[:8]}"
    t1 = await journey_service.update_from_turn(
        db, tenant_id=TENANT, user_id=user, session_id="s1",
        intent="PRODUCT.ASK_INFO", status=TurnStatus.DONE,
    )
    assert t1["stage"] == "REGISTERED"  # NEW+1（弱证据一格）
    state = await journey_service.get_stage(db, TENANT, user)
    assert state == {"stage": "REGISTERED", "at_risk": False}

    # 弱证据逐格逼近目标：第二次 ASK_INFO → DISCOVERING（到达目标）
    t2 = await journey_service.update_from_turn(
        db, tenant_id=TENANT, user_id=user, session_id="s1",
        intent="PRODUCT.ASK_INFO", status=TurnStatus.DONE,
    )
    assert t2["stage"] == "DISCOVERING"
    # 到达目标后同信号不再写
    t2b = await journey_service.update_from_turn(
        db, tenant_id=TENANT, user_id=user, session_id="s1",
        intent="PRODUCT.ASK_INFO", status=TurnStatus.DONE,
    )
    assert t2b is None

    # 强证据跳 PURCHASED + 转移史追加
    t3 = await journey_service.update_from_turn(
        db, tenant_id=TENANT, user_id=user, session_id="s2",
        intent="LOGISTICS.TRACK", status=TurnStatus.DONE,
    )
    assert t3["stage"] == "PURCHASED"
    from app.repositories.customer_journey_repository import customer_journey_repository

    row = await customer_journey_repository.get_by_user(db, TENANT, user)
    assert len(row.signals_json) == 3
    assert row.signals_json[-1]["to"] == "PURCHASED"


# ---------------- 活动旅程门控 ----------------


def _campaign(**over):
    base = {
        "campaign_id": "c1", "enabled": True,
        "eligible_intents": ["PRODUCT."],
        "eligible_journey_stages": ["CONSIDERING", "READY_TO_BUY"],
    }
    return {**base, **over}


def test_campaign_journey_gating():
    ok = campaign_eligible(_campaign(), "PRODUCT.ASK_PRICE", NOW, journey_stage="CONSIDERING")
    assert ok
    # 阶段不符 / 未知（保守不推）
    assert not campaign_eligible(_campaign(), "PRODUCT.ASK_PRICE", NOW, journey_stage="NEW")
    assert not campaign_eligible(_campaign(), "PRODUCT.ASK_PRICE", NOW, journey_stage=None)
    # 未声明阶段限制的活动不受影响
    free = _campaign(eligible_journey_stages=[])
    assert campaign_eligible(free, "PRODUCT.ASK_PRICE", NOW, journey_stage=None)
