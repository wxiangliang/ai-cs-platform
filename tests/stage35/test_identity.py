"""Stage 35 身份核验分级回归测试。

锁定四件事：
1. 目录声明契约：每个工具都有 min_ial；改地址=IAL2、政策查询=IAL0、
   涉个人数据读=IAL1、未收录工具保守 IAL1；
2. 等级推导：沙盒默认互信（零回归）；鉴权是加成取 max 不降级；
3. 写侧执法：等级不足 executor 拒绝（IDENTITY_REQUIRED），且在防重放
   之前拦截（不消耗执行权）；
4. 触发映射：IDENTITY_VERIFY 有 Case 映射。
"""


import pytest

import app.core.identity as identity_mod
from app.chat.tools.catalog import TOOL_CATALOG, required_ial
from app.core.config import settings
from app.core.identity import (
    current_identity_level,
    identity_sufficient,
    set_identity_level,
)
from app.services.case_service import REASON_CASE_MAP


@pytest.fixture(autouse=True)
def _reset_identity_context():
    """每例后清空等级 contextvar：防止本文件设置的等级泄漏到其他测试
    （monkeypatch 只恢复 settings，contextvar 需显式复位到「未设置」）。"""
    yield
    identity_mod._current_ial.set(None)


# ---------------- 目录声明契约 ----------------


def test_all_tools_declare_ial():
    for tool_id, meta in TOOL_CATALOG.items():
        assert isinstance(meta.min_ial, int) and 0 <= meta.min_ial <= 3, tool_id


def test_ial_assignments():
    assert required_ial("update_order_address") == 2  # 改地址=盗号攻击面
    assert required_ial("query_refund_policy") == 0  # 公开政策
    assert required_ial("query_order") == 1  # 涉个人数据
    assert required_ial("create_refund_ticket") == 1
    assert required_ial("query_logistics") == 1  # alias 归一
    assert required_ial("some_unknown_tool") == 1  # 未收录保守处理


# ---------------- 等级推导 ----------------


def test_default_level_permissive_in_dev():
    """沙盒默认互信（IDENTITY_DEFAULT_LEVEL=2）——全量既有流程零回归。"""
    assert settings.IDENTITY_DEFAULT_LEVEL == 2
    set_identity_level(authenticated=False)
    assert current_identity_level() == 2
    assert identity_sufficient("update_order_address")


def test_authenticated_is_bonus_not_downgrade(monkeypatch):
    """鉴权是加成取 max：开启鉴权绝不把等级降到比渠道基线低。"""
    set_identity_level(authenticated=True)
    assert current_identity_level() == max(
        settings.IDENTITY_DEFAULT_LEVEL, settings.IDENTITY_LEVEL_AUTHENTICATED
    )
    # 生产形态：基线降到 0，鉴权提供 IAL1
    monkeypatch.setattr(settings, "IDENTITY_DEFAULT_LEVEL", 0)
    set_identity_level(authenticated=True)
    assert current_identity_level() == 1
    assert identity_sufficient("query_order")
    assert not identity_sufficient("update_order_address")  # IAL2 不可达 → 转人工
    set_identity_level(authenticated=False)
    assert current_identity_level() == 0
    assert not identity_sufficient("query_order")


# ---------------- 写侧执法 ----------------


async def test_executor_rejects_insufficient_identity(monkeypatch):
    """等级不足：executor 返回 IDENTITY_REQUIRED，且不需要 task_id/DB——
    证明拦截发生在防重放校验之前（不消耗执行权）。"""
    from app.chat.actions.executor import action_executor

    monkeypatch.setattr(settings, "IDENTITY_DEFAULT_LEVEL", 1)
    set_identity_level(authenticated=False)
    outcome = await action_executor.execute(
        None,  # 等级拦截先于任何 DB 访问，session 不会被触碰
        tenant_id="t1",
        session_id="s1",
        task={"intent": "ORDER.CHANGE_ADDRESS",
              "collected_slots": {"order_id": "SO1", "address": "x"}},
    )
    assert outcome.ok is False and outcome.error_code == "IDENTITY_REQUIRED"
    # 恢复沙盒基线，避免影响后续用例
    set_identity_level(authenticated=False)


def test_identity_verify_reason_mapped_to_case():
    assert REASON_CASE_MAP["IDENTITY_VERIFY"] == ("POLICY_REVIEW", "NORMAL")
