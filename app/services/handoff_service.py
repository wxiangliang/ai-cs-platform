"""HandoffService —— 转人工触发收口与坐席操作（Stage 07）。

所有转人工路径统一走 ensure_ticket（幂等：同会话未关闭工单不重复建单）：
USER_REQUEST（META.TRANSFER_HUMAN）/ PAYMENT_ISSUE / REPEATED_UNKNOWN（连续兜底）/
EXECUTION_FAILED（写工具失败）/ SKILL_RULE（requires_human_if 机读条件）/ MANUAL。

context_json 上下文移交包：任务栈快照、已收集槽位、最近 8 条消息摘要、
最后一轮检索/工具轨迹——坐席打开工单即懂上下文，不让用户复述；
脱敏口径与 chat_tool_call 一致（手机号打码）。
"""

from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.tools.base import mask_sensitive
from app.core.logging import get_logger
from app.repositories.chat_dialog_state_repository import chat_dialog_state_repository
from app.repositories.chat_handoff_ticket_repository import chat_handoff_ticket_repository
from app.repositories.chat_message_repository import chat_message_repository
from app.repositories.chat_session_repository import chat_session_repository

logger = get_logger(__name__)

# 触发原因枚举
REASON_USER_REQUEST = "USER_REQUEST"
REASON_SKILL_RULE = "SKILL_RULE"
REASON_PAYMENT_ISSUE = "PAYMENT_ISSUE"
REASON_REPEATED_UNKNOWN = "REPEATED_UNKNOWN"
REASON_EXECUTION_FAILED = "EXECUTION_FAILED"
REASON_MANUAL = "MANUAL"
REASON_ABUSE = "ABUSE"  # 重度违禁连击（Stage 14 护栏）

_CONTEXT_MESSAGES = 8


class HandoffService:
    """转人工闭环服务。"""

    async def ensure_ticket(
        self,
        session: AsyncSession,
        *,
        tenant_id: str,
        session_id: str,
        user_id: str,
        reason: str,
        source_intent: str | None = None,
        extra_context: dict[str, Any] | None = None,
    ) -> tuple[str, bool]:
        """幂等建单：返回 (ticket_id, 是否新建)。"""
        existing = await chat_handoff_ticket_repository.get_open_by_session(
            session, tenant_id, session_id
        )
        if existing is not None:
            return existing.id, False

        context = await self._build_context(session, tenant_id, session_id)
        if extra_context:
            context.update(extra_context)
        # SAVEPOINT 包裹建单（Stage 13）：并发窗口撞部分唯一索引时只回滚
        # 保存点，不让 IntegrityError 把主事务打成 aborted（否则殃及同事务的
        # 消息/决策日志落库）；撞索引即说明别处已建单，回查返回（幂等语义补全）
        try:
            async with session.begin_nested():
                ticket = await chat_handoff_ticket_repository.create(
                    session,
                    tenant_id=tenant_id,
                    session_id=session_id,
                    user_id=user_id,
                    reason=reason,
                    source_intent=source_intent,
                    context_json=context,
                )
        except IntegrityError:
            existing = await chat_handoff_ticket_repository.get_open_by_session(
                session, tenant_id, session_id
            )
            if existing is not None:
                return existing.id, False
            raise
        logger.info(
            "handoff ticket created: id=%s reason=%s session=%s", ticket.id, reason, session_id
        )
        # 指标：建单计数（仅新建，幂等命中不重复计）
        from app.core.metrics import count_handoff

        count_handoff(reason)
        # 坐席侧实时推送（Stage 15）：新工单进队列（publish 内部 fail-open）
        from app.services.notify_service import agents_channel, ws_hub

        ws_hub.publish_after_commit(
            session,
            agents_channel(tenant_id),
            {"type": "ticket_created", "ticket_id": ticket.id, "session_id": session_id,
             "reason": reason, "source_intent": source_intent},
        )
        return ticket.id, True

    async def queue_position(self, session: AsyncSession, tenant_id: str, ticket_id: str) -> int:
        """工单在 PENDING 队列中的位置（1 起；排队反馈话术用）。"""
        return await chat_handoff_ticket_repository.count_pending_before(
            session, tenant_id, ticket_id
        )

    async def _build_context(
        self, session: AsyncSession, tenant_id: str, session_id: str
    ) -> dict[str, Any]:
        """组装上下文移交包（脱敏）。"""
        context: dict[str, Any] = {}
        # 任务栈与槽位快照
        dialog_state = await chat_dialog_state_repository.get_by_session_id(
            session, tenant_id, session_id
        )
        if dialog_state is not None:
            if dialog_state.active_task_json:
                context["active_task"] = mask_sensitive(dict(dialog_state.active_task_json))
            if dialog_state.task_stack_json:
                context["task_stack"] = [
                    mask_sensitive(dict(t)) for t in dialog_state.task_stack_json
                ]
            context["dialog_state"] = dialog_state.state
        # 最近消息摘要（倒序取正序放）
        messages = await chat_message_repository.list_history_page(
            session, tenant_id, session_id, limit=_CONTEXT_MESSAGES
        )
        context["recent_messages"] = [
            mask_sensitive({"role": m.role, "content": m.content[:200], "intent": m.intent})
            for m in reversed(messages)
        ]
        return context

    # ------------------------------------------------------------------
    # 坐席操作
    # ------------------------------------------------------------------
    async def claim(
        self, session: AsyncSession, tenant_id: str, ticket_id: str, assignee: str
    ) -> bool:
        """认领：仅 PENDING 可认领（条件更新防并发抢单）。"""
        return await chat_handoff_ticket_repository.claim(
            session, tenant_id, ticket_id, assignee
        )

    async def reply(
        self, session: AsyncSession, tenant_id: str, ticket_id: str, content: str
    ) -> str | None:
        """坐席回复：以 role=agent 写入会话消息，返回消息 id（工单不存在返回 None）。"""
        ticket = await chat_handoff_ticket_repository.get_owned(session, tenant_id, ticket_id)
        if ticket is None or ticket.status not in ("PENDING", "ASSIGNED"):
            return None
        message = await chat_message_repository.create(
            session,
            tenant_id=tenant_id,
            session_id=ticket.session_id,
            role="agent",
            content=content,
            status="HANDOFF",
        )
        # 用户侧实时推送（Stage 15）：坐席回复秒达，不再依赖拉历史
        from app.services.notify_service import session_channel, ws_hub

        ws_hub.publish_after_commit(
            session,
            session_channel(tenant_id, ticket.session_id),
            {"type": "agent_reply", "message_id": message.id, "content": content,
             "created_at": message.created_at.isoformat() if message.created_at else None},
        )
        return message.id

    async def resolve(
        self, session: AsyncSession, tenant_id: str, ticket_id: str
    ) -> bool:
        """解决归还：工单 RESOLVED，会话恢复 active、状态机复位 IDLE（bot 恢复应答）。"""
        from datetime import datetime, timezone

        ticket = await chat_handoff_ticket_repository.get_owned(session, tenant_id, ticket_id)
        if ticket is None or ticket.status not in ("PENDING", "ASSIGNED"):
            return False
        ticket.status = "RESOLVED"
        ticket.resolved_at = datetime.now(timezone.utc)
        # 会话归还：bot 恢复
        await chat_session_repository.update_status(
            session, tenant_id, ticket.session_id, "active"
        )
        # 状态机复位：清任务与栈（人工已处理完，不自动续接旧任务）；
        # 同时置 csat_pending（Stage 15）：下一条用户消息若是评分即捕获
        await chat_dialog_state_repository.upsert_by_session_id(
            session,
            tenant_id,
            ticket.session_id,
            state="IDLE",
            active_task_json=None,
            task_stack_json=[],
            context_stacks_json={"csat_pending": "handoff_resolve"},
        )
        # 终结未完成的 chat_task 行：清引用后 TTL 自愈够不到，须显式 ABORTED（Stage 15）
        from app.repositories.chat_task_repository import chat_task_repository

        await chat_task_repository.abort_open_by_session(session, tenant_id, ticket.session_id)
        # CSAT 询问（Stage 15）：以 assistant 消息发出（历史可见 + WS 推送）；
        # locale 取会话记住的语言（Stage 19，坐席归还无用户请求上下文）
        from app.core.i18n import t

        sess = await chat_session_repository.get_by_id(session, ticket.session_id)
        loc = (sess.metadata_json or {}).get("locale") if sess is not None else None
        csat_msg = await chat_message_repository.create(
            session,
            tenant_id=tenant_id,
            session_id=ticket.session_id,
            role="assistant",
            content=t("csat.ask_handoff", loc),
            status="DONE",
            metadata_json={"csat_request": "handoff_resolve"},
        )
        await session.flush()
        # 用户侧实时推送：会话归还 + CSAT 询问
        from app.services.notify_service import session_channel, ws_hub

        ws_hub.publish_after_commit(
            session,
            session_channel(tenant_id, ticket.session_id),
            {"type": "session_resumed", "ticket_id": ticket_id,
             "csat_message_id": csat_msg.id, "content": csat_msg.content},
        )
        return True


# 模块级单例
handoff_service = HandoffService()
