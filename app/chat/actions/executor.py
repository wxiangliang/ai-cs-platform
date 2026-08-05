"""ActionExecutor —— 写操作执行器（Stage 05）。

红线：本类是系统中**唯一**允许调用写工具（create_*/cancel_*/update_*）的入口；
LLM / 回复节点 / 其他任何路径不得直接调用写工具。

执行前二次校验（防御纵深，即使上游路由有 bug 也不误执行）：
1. Skill 必须是 WRITE 且声明了 requires_confirmation 的 action；
2. 必填槽位齐全；
3. 防重放：任务在 chat_task 表中的状态必须是 CONFIRMING（已执行过的任务
   状态为 DONE/EXECUTING，直接拒绝二次执行）。

事务边界（与真实副作用解耦）：先用**独立事务**把任务标记为 EXECUTING 并提交，
再调用工具；工具结果在主事务中更新为 DONE/FAILED——即使主事务最终回滚，
EXECUTING 标记也能证明"工具可能已被调用"，避免外部副作用无痕。
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.skills.registry import skill_registry
from app.chat.skills.types import SkillKind
from app.chat.tools.base import ToolResult, mask_sensitive
from app.chat.tools.factory import get_tool_provider
from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.repositories.chat_task_repository import chat_task_repository
from app.repositories.chat_tool_call_repository import chat_tool_call_repository

logger = get_logger(__name__)


@dataclass
class ExecutionOutcome:
    """执行结果。"""

    ok: bool
    ticket_no: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    tool_id: str | None = None


class ActionExecutor:
    """写操作执行器。"""

    async def execute(
        self,
        session: AsyncSession,
        *,
        tenant_id: str,
        session_id: str,
        task: dict[str, Any],
        user_id: str = "",
    ) -> ExecutionOutcome:
        """执行一个确认通过的任务（finished_task）。校验失败返回错误码，不抛异常。"""
        intent = task.get("intent", "")
        slots: dict[str, Any] = task.get("collected_slots", {})
        skill = skill_registry.get(intent)

        # —— 校验 1：必须是 WRITE 技能且声明了需确认的 action ——
        if skill.kind != SkillKind.WRITE or not skill.actions:
            logger.warning("action executor rejected: intent=%s 非写技能或未声明 action", intent)
            return ExecutionOutcome(ok=False, error_code="NO_ACTION_DECLARED")
        action = skill.actions[0]
        if not action.requires_confirmation:
            # 声明为无确认的 action 也从本入口走（统一审计），只是不查确认状态
            logger.info("action %s 声明无需确认，直接执行", action.action_id)

        # —— 校验 2：必填槽位齐全 ——
        missing = [s for s in skill.required_slots if not slots.get(s)]
        if missing:
            logger.warning("action executor rejected: intent=%s 缺槽位 %s", intent, missing)
            return ExecutionOutcome(ok=False, error_code="MISSING_SLOTS")

        # —— 校验 3：防重放（Stage 13 原子化整改）——
        # 无 task_id 的任务无法防重放，一律拒绝（正常流程 task 行在创建轮即持久化）
        task_id = task.get("task_id")
        if not task_id:
            logger.warning("action executor rejected: intent=%s 任务缺 task_id，无法防重放", intent)
            return ExecutionOutcome(ok=False, error_code="NO_TASK_ID")

        # 原子拿执行权（独立事务，先于真实副作用提交）：
        # 条件 UPDATE 只有一个并发请求能赢——「检查再执行」两步在并发确认下
        # 会双重执行退款/取消（审计 P1），故拿不到执行权/标记失败都必须中止
        try:
            async with AsyncSessionLocal() as mark_session:
                claimed = await chat_task_repository.claim_for_execution(
                    mark_session, tenant_id, task_id
                )
                await mark_session.commit()
        except Exception:  # noqa: BLE001 - 执行权状态未知时宁可不执行
            logger.exception("claim task for execution failed: task=%s", task_id)
            return ExecutionOutcome(ok=False, error_code="CLAIM_FAILED")
        if not claimed:
            # 拿不到执行权：区分三种情形——
            # 1) 并发在途兄弟请求（confirmed_at≈now）：赢家即将写成功 → ALREADY_EXECUTED
            #    「此前已提交成功」话术安全（双击/重试的第二次）；
            # 2) 卡在 EXECUTING 且 confirmed_at 过期（上次 invoke 后主事务回滚，
            #    DONE/FAILED 未落）→ 不能谎报成功，交人工核实真实结果（审计 P2 修复）；
            # 3) 已 DONE/FAILED → ALREADY_EXECUTED。
            row = await chat_task_repository.get_owned(session, tenant_id, task_id)
            if row is not None and row.status == "EXECUTING":
                stale_after = settings.TOOL_TIMEOUT + 10  # 超过一次调用上限即认定被中断
                confirmed = row.confirmed_at
                if confirmed is not None:
                    age = (datetime.now(timezone.utc) - confirmed).total_seconds()
                    if age > stale_after:
                        logger.warning("action executor: task=%s 卡 EXECUTING %ds（被中断）",
                                       task_id, int(age))
                        return ExecutionOutcome(ok=False, error_code="EXECUTION_INTERRUPTED")
            logger.warning("action executor rejected: task=%s 未拿到执行权（已执行/并发确认）", task_id)
            return ExecutionOutcome(ok=False, error_code="ALREADY_EXECUTED")

        # —— 调用写工具（唯一入口）——
        provider = get_tool_provider()
        # 端到端幂等契约（生产就绪审计 2026-08-05）：task_id 作为幂等键随写操作
        # 下发——外部系统按此键去重，「我方超时但对方已提交」的重试不产生重复
        # 业务动作（对接契约见 stage-11 文档第 6 节；mock 端天然确定性）
        # user_id 与幂等键同为系统上下文（会员类写操作按用户记账；无关工具忽略）
        params = {**slots, "idempotency_key": task_id}
        if user_id:
            params["user_id"] = user_id
        result: ToolResult = await provider.invoke(action.action_id, params, tenant_id=tenant_id)

        # —— 审计落库（主事务）——
        await chat_tool_call_repository.create(
            session,
            tenant_id=tenant_id,
            session_id=session_id,
            task_id=task_id,
            tool_id=action.action_id,
            request_json=mask_sensitive(params),
            response_json=result.data if result.ok else None,
            ok=result.ok,
            error_code=result.error_code,
            latency_ms=result.latency_ms,
        )

        # —— 更新任务行（主事务）——
        # 注意：EXECUTING 标记由独立事务提交过，行版本号已+1；
        # 主事务身份映射里缓存的是旧版本，必须先 refresh 再更新，否则撞乐观锁
        if task_id:
            row = await chat_task_repository.get_owned(session, tenant_id, task_id)
            if row is not None:
                await session.refresh(row)
                row.status = "DONE" if result.ok else "FAILED"
                row.executed_at = datetime.now(timezone.utc)
                row.result_json = result.data if result.ok else {"error_code": result.error_code}
                await session.flush()

        # 指标：写操作执行计数（Stage 09）
        from app.core.metrics import count_action

        count_action(action.action_id, result.ok)

        if not result.ok:
            return ExecutionOutcome(
                ok=False, error_code=result.error_code, tool_id=action.action_id
            )
        return ExecutionOutcome(
            ok=True,
            ticket_no=str(result.data.get("ticket_no") or ""),
            data=result.data,
            tool_id=action.action_id,
        )


# 模块级单例
action_executor = ActionExecutor()
