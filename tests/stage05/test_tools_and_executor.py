"""工具层与 ActionExecutor 单元测试（Stage 05）。"""

from unittest.mock import AsyncMock

from app.chat.actions.executor import action_executor
from app.chat.intent.types import IntentLabel
from app.chat.skills.loader import load_skill_declarations
from app.chat.skills.registry import skill_registry
from app.chat.tools.base import mask_sensitive
from app.chat.tools.mock_provider import mock_tool_provider


async def test_mock_provider_deterministic():
    r1 = await mock_tool_provider.invoke("create_refund_ticket", {"order_id": "A1"}, tenant_id="t1")
    r2 = await mock_tool_provider.invoke("create_refund_ticket", {"order_id": "A1"}, tenant_id="t1")
    assert r1.ok and r1.data["ticket_no"] == r2.data["ticket_no"]  # 同入参恒定同输出
    r3 = await mock_tool_provider.invoke("create_refund_ticket", {"order_id": "A2"}, tenant_id="t1")
    assert r3.data["ticket_no"] != r1.data["ticket_no"]


async def test_mock_provider_unknown_tool():
    r = await mock_tool_provider.invoke("no_such_tool", {}, tenant_id="t1")
    assert not r.ok and r.error_code == "TOOL_NOT_FOUND"


def test_mask_sensitive_phone():
    masked = mask_sensitive({"phone": "13800138000", "note": "联系 13912345678 送货"})
    assert masked["phone"] == "138****8000"
    assert "139****5678" in masked["note"]


def test_skill_loader_declarations_complete():
    """全部 33 个技能 md 可加载且校验通过（Stage 33 增 MEMBER.REGISTER、统一批增 META.DENY）；写技能带 action 声明。"""
    decls = load_skill_declarations()
    assert len(decls) == 33
    for intent in ("AFTERSALE.REFUND", "ORDER.CANCEL", "PAYMENT.INVOICE"):
        assert decls[intent]["actions"], f"{intent} 缺 action 声明"
        assert decls[intent]["risk_level"] == "L3"
    # 合并进注册表生效
    assert skill_registry.get("AFTERSALE.REFUND").actions[0].action_id == "create_refund_ticket"


async def test_executor_rejects_non_write_skill():
    task = {"intent": IntentLabel.CHITCHAT_GENERAL, "collected_slots": {}}
    outcome = await action_executor.execute(None, tenant_id="t1", session_id="s1", task=task)
    assert not outcome.ok and outcome.error_code == "NO_ACTION_DECLARED"


async def test_executor_rejects_missing_slots():
    task = {"intent": IntentLabel.AFTERSALE_REFUND, "collected_slots": {}}
    outcome = await action_executor.execute(None, tenant_id="t1", session_id="s1", task=task)
    assert not outcome.ok and outcome.error_code == "MISSING_SLOTS"


async def test_executor_rejects_replay(monkeypatch):
    """任务行已是 DONE → 拒绝二次执行（防重放）。"""

    class _Row:
        status = "DONE"

    monkeypatch.setattr(
        "app.chat.actions.executor.chat_task_repository.get_owned",
        AsyncMock(return_value=_Row()),
    )
    task = {
        "intent": IntentLabel.AFTERSALE_REFUND,
        "collected_slots": {"order_id": "A1"},
        "task_id": "task-x",
    }
    outcome = await action_executor.execute(None, tenant_id="t1", session_id="s1", task=task)
    assert not outcome.ok and outcome.error_code == "ALREADY_EXECUTED"
    # claim_for_execution 走了真实 DB（独立事务），释放引擎防跨模块事件循环污染
    from app.db.session import dispose_engine

    await dispose_engine()
