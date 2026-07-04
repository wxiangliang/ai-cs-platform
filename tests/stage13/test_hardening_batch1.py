"""Stage 13 第一批整改单元测试：配置门禁 / 防重放原子化 / 管理面 token。"""

import asyncio
import uuid

import pytest

from app.chat.actions import executor as executor_mod
from app.chat.actions.executor import action_executor
from app.chat.intent.types import IntentLabel
from app.chat.tools.base import ToolResult
from app.core.auth import require_admin
from app.core.config import Settings, settings
from app.core.exceptions import AppException
from app.db.session import AsyncSessionLocal, dispose_engine
from app.repositories.chat_session_repository import chat_session_repository
from app.repositories.chat_task_repository import chat_task_repository

TENANT = "hard-t"


# ---------------------------------------------------------------------------
# 2.1 生产配置硬门禁
# ---------------------------------------------------------------------------


def test_prod_gate_rejects_default_config():
    """APP_ENV=prod + 默认配置 → 拒绝启动并列出全部缺项。"""
    with pytest.raises(Exception) as exc:
        Settings(APP_ENV="prod", _env_file=None)
    msg = str(exc.value)
    assert "AUTH_ENABLED" in msg and "hash" in msg and "postgres:postgres" in msg


def test_prod_gate_passes_with_secure_config():
    s = Settings(
        APP_ENV="prod",
        AUTH_ENABLED=True,
        EMBEDDING_PROVIDER="openai",
        DATABASE_URL="postgresql+asyncpg://svc:strongpass@db:5432/ai_cs",
        _env_file=None,
    )
    assert s.APP_ENV == "prod"


def test_local_env_not_gated():
    """本地开发默认配置不受门禁影响（零回归）。"""
    s = Settings(APP_ENV="local", _env_file=None)
    assert s.AUTH_ENABLED is False


# ---------------------------------------------------------------------------
# 2.1 开发模式管理面必须配置 token
# ---------------------------------------------------------------------------


async def test_admin_requires_token_in_dev_mode(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_ENABLED", False)
    monkeypatch.setattr(settings, "KB_ADMIN_TOKEN", "")
    with pytest.raises(AppException) as exc:
        await require_admin(auth=None, x_kb_admin_token=None)
    assert exc.value.error_code == "ADMIN_TOKEN_REQUIRED"

    monkeypatch.setattr(settings, "KB_ADMIN_TOKEN", "dev-token")
    # 带对 token 放行；带错 token 拒绝
    assert await require_admin(auth=None, x_kb_admin_token="dev-token") is None
    with pytest.raises(AppException):
        await require_admin(auth=None, x_kb_admin_token="wrong")


# ---------------------------------------------------------------------------
# 2.2 ActionExecutor 防重放原子化
# ---------------------------------------------------------------------------


@pytest.fixture
async def _confirming_task():
    """造一个 CONFIRMING 状态的退款任务行。"""
    async with AsyncSessionLocal() as session:
        record = await chat_session_repository.create(
            session, tenant_id=TENANT, user_id=f"u-{uuid.uuid4().hex[:6]}", channel="web"
        )
        sid = record.id
        row = await chat_task_repository.create(
            session,
            tenant_id=TENANT,
            session_id=sid,
            intent=IntentLabel.AFTERSALE_REFUND,
            skill_id="aftersale_refund",
            status="CONFIRMING",
            collected_slots_json={"order_id": "SO-HARD-1"},
        )
        task_id = row.id
        await session.commit()
    yield sid, task_id
    await dispose_engine()


async def test_executor_rejects_missing_task_id():
    task = {"intent": IntentLabel.AFTERSALE_REFUND, "collected_slots": {"order_id": "A1"}}
    outcome = await action_executor.execute(None, tenant_id=TENANT, session_id="s1", task=task)
    assert not outcome.ok and outcome.error_code == "NO_TASK_ID"


async def test_concurrent_confirm_executes_exactly_once(monkeypatch, _confirming_task):
    """并发两条「确认」同时执行同一任务：写工具恰好被调用一次。"""
    sid, task_id = _confirming_task
    calls: list[str] = []

    class _CountingProvider:
        name = "counting"

        async def invoke(self, tool_id, params, *, tenant_id):
            calls.append(tool_id)
            await asyncio.sleep(0.05)  # 放大并发窗口
            return ToolResult(ok=True, data={"ticket_no": "TK1"}, latency_ms=1.0)

    monkeypatch.setattr(executor_mod, "get_tool_provider", lambda: _CountingProvider())
    task = {
        "intent": IntentLabel.AFTERSALE_REFUND,
        "collected_slots": {"order_id": "SO-HARD-1"},
        "task_id": task_id,
    }

    async def _run():
        async with AsyncSessionLocal() as session:
            outcome = await action_executor.execute(
                session, tenant_id=TENANT, session_id=sid, task=dict(task)
            )
            await session.commit()
            return outcome

    o1, o2 = await asyncio.gather(_run(), _run())
    assert len(calls) == 1, f"写工具应恰好调用一次，实际 {len(calls)} 次"
    oks = sorted([o1.ok, o2.ok])
    assert oks == [False, True]
    failed = o1 if not o1.ok else o2
    assert failed.error_code == "ALREADY_EXECUTED"


async def test_claim_only_from_intermediate_status(_confirming_task):
    """已 DONE 的任务拿不到执行权。"""
    _, task_id = _confirming_task
    async with AsyncSessionLocal() as session:
        assert await chat_task_repository.claim_for_execution(session, TENANT, task_id) is True
        row = await chat_task_repository.get_owned(session, TENANT, task_id)
        row.status = "DONE"
        await session.flush()
        await session.refresh(row)
        assert await chat_task_repository.claim_for_execution(session, TENANT, task_id) is False
        await session.rollback()
