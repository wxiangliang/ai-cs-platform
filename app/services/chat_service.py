"""ChatService —— 聊天应用服务层。

职责：协调 Repository 与 LangGraph 主链路。
- 生成 trace_id 并写入日志上下文；
- 组装图初始状态；
- 调用编译后的主链路图；
- 把图最终状态整理成 API 响应数据；
- （v2）并发冲突转业务错误码；任一节点失败时用独立事务写带错误信息的
  decision_log，保证失败轮次可观测（业务事务本身回滚，不写脏数据）。

不在此写具体决策逻辑（决策在 Graph 节点 / 各业务模块中）。
"""

import time
from uuid import uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from app.chat.graph.builder import get_chat_graph
from app.chat.llm.budget import set_current_tenant
from app.chat.llm.deadline import start_turn_budget
from app.chat.logging.decision_logger import build_log_data, decision_logger
from app.chat.memory.scheduler import (
    enqueue_memory_after_commit,
    wait_pending_memory_tasks,
)
from app.core.config import settings
from app.core.exceptions import AppException
from app.core.i18n import resolve_locale
from app.core.logging import get_logger, get_trace_id, set_trace_id
from app.core.metrics import observe_turn
from app.core.tracing import build_trace_metadata, get_langfuse_handler
from app.db.session import AsyncSessionLocal
from app.experiments.resolver import resolve_experiment, set_overrides
from app.repositories.chat_message_repository import chat_message_repository
from app.repositories.chat_session_repository import chat_session_repository
from app.schemas.chat import (
    ChatMessageData,
    ChatMessageRequest,
    FeedbackRequest,
    HistoryMessageItem,
    SessionCreateRequest,
    SessionData,
)

logger = get_logger(__name__)


async def wait_background_tasks(timeout: float = 5.0) -> None:
    """应用关停时收敛记忆后台任务，保持既有 lifespan 调用接口。"""
    await wait_pending_memory_tasks(timeout)


class ChatService:
    """聊天主链路应用服务。"""

    async def process_message(
        self,
        session: AsyncSession,
        session_id: str,
        req: ChatMessageRequest,
        tenant_id: str,
    ) -> ChatMessageData:
        """处理一条用户消息，返回结构化结果。

        tenant_id 由路由层解析（鉴权开启时来自凭证，开发模式来自请求体），
        本方法不再读 req.tenant_id。
        """
        # trace_id 由入口中间件生成（Stage 08）；无中间件场景（脚本直调）兜底生成
        trace_id = get_trace_id()
        if not trace_id or trace_id == "-":
            trace_id = "trace_" + uuid4().hex
            set_trace_id(trace_id)
        # 预算归属（Stage 17）：写入当前租户，供 LLM 收口计量与熔断；
        # 提交后记忆任务会显式恢复租户上下文
        set_current_tenant(tenant_id)
        # 身份等级注入（Stage 35）：AUTH_ENABLED 下能到这里的请求都已过
        # chat scope 鉴权=渠道身份成立；开发模式走沙盒基线（默认互信）
        from app.core.identity import set_identity_level

        set_identity_level(authenticated=settings.AUTH_ENABLED)
        # 行为准则命中收集器（Stage 40）：turn 级清零，save_turn 落决策日志
        from app.chat.guidelines import reset_matched_guidelines

        reset_matched_guidelines()
        # 轮级 LLM 时间预算（容量修复）：本轮所有 LLM 调用共享 deadline，
        # 耗尽后各调用点走既有降级路径（与无 Key 同路径），单轮时长有界
        start_turn_budget()

        # A/B 实验分流（Stage 18）：确定性分桶命中变体，把参数覆盖写入 contextvar
        # （白名单消费点经 effective() 读取），分配落 state 供 decision_log 切分；
        # 无实验/解析失败 → 空覆盖走默认参数（零回归）
        resolution = resolve_experiment(tenant_id, session_id)
        set_overrides(resolution.overrides)
        logger.info(
            "chat process_message: session=%s tenant=%s channel=%s",
            session_id,
            tenant_id,
            req.channel,
        )

        # 组装图初始状态（全部可序列化，为 checkpointer 铺路）；
        # db session 等运行期依赖通过 LangGraph config 注入
        initial_state = {
            "tenant_id": tenant_id,
            "session_id": session_id,
            "user_id": req.user_id,
            "channel": req.channel,
            "message": req.message,
            "trace_id": trace_id,
            "start_ts": time.perf_counter(),
            "graph_trace": [],
            # 语言（Stage 19）：locale 兜底值 + 请求原值透传给 load_session_state
            # 结合会话记忆决策（请求带的优先，否则会话记住的，否则默认）
            "locale": resolve_locale(req.locale),
            "locale_req": req.locale,
            # A/B 实验变体分配（Stage 18）：无命中为 None
            "experiment": resolution.to_log(),
        }
        run_config: dict = {"configurable": {"db_session": session}}

        # —— Langfuse 链路追踪（Stage 12）：一轮对话一条 trace，节点/LLM 自动成 span；
        # 未配置 Key 时 handler 为 None，零开销跳过 ——
        handler = get_langfuse_handler()
        if handler is not None:
            run_config["callbacks"] = [handler]
            run_config["run_name"] = "chat_turn"
            run_config["metadata"] = build_trace_metadata(
                session_id=session_id,
                user_id=req.user_id,
                tenant_id=tenant_id,
                channel=req.channel,
                trace_id=trace_id,
            )

        # 执行编译后的主链路图（节点内部完成读状态、识别、抽槽、流转、回复、落库）
        graph = get_chat_graph()
        try:
            final_state = await graph.ainvoke(initial_state, config=run_config)
        except (StaleDataError, IntegrityError) as exc:
            # 并发冲突：同一会话两条消息同时处理（乐观锁 version 不命中），
            # 或首轮消息并发插入撞唯一约束。业务事务由依赖层回滚。
            await self._log_failed_turn(initial_state, "CONCURRENT_UPDATE", exc)
            raise AppException(
                message="该会话正在处理另一条消息，请稍后重试",
                error_code="CONCURRENT_UPDATE",
                status_code=409,
            ) from exc
        except AppException as exc:
            # 业务异常（如会话归属校验失败）：留痕后原样抛出
            await self._log_failed_turn(initial_state, exc.error_code, exc)
            raise
        except Exception as exc:
            # 未预期异常：留痕后交给全局兜底 handler（500）
            await self._log_failed_turn(initial_state, "INTERNAL_ERROR", exc)
            raise

        intent_dict = final_state.get("intent_result") or {}
        final_intent = intent_dict.get("final_intent") or intent_dict.get("pred_label")
        reply = final_state.get("reply", "")

        # 指标：轮次耗时（意图域 × 回复分支，Stage 09 收口点）
        start_ts: float = initial_state["start_ts"]  # type: ignore[assignment]
        observe_turn(
            final_intent, final_state.get("answer_source"), time.perf_counter() - start_ts,
        )

        # —— 记忆写入（Stage 10）：提交后异步 best-effort ——
        # 此时图已完成但请求事务尚未提交；只登记参数，commit 后才创建后台任务。
        enqueue_memory_after_commit(
            session,
            tenant_id=tenant_id,
            user_id=req.user_id,
            session_id=session_id,
            user_text=req.message,
            reply=reply,
            intent=final_intent,
        )

        return ChatMessageData(
            message_id=final_state.get("user_message_id", ""),
            session_id=session_id,
            reply=reply,
            intent=final_intent,
            status=final_state.get("status"),
            state=final_state.get("new_state") or final_state.get("current_state"),
            slots=final_state.get("slots") or {},
            trace_id=trace_id,
        )

    async def create_session(
        self, session: AsyncSession, req: SessionCreateRequest, tenant_id: str
    ) -> SessionData:
        """创建会话（服务端生成 session_id，见 chat_api.md 第 6 节规划）。"""
        record = await chat_session_repository.create(
            session,
            tenant_id=tenant_id,
            user_id=req.user_id,
            channel=req.channel,
        )
        # —— Stage 41 开场引导（默认关）：用户还没说话时的轻量欢迎。
        # 抑制/频控/旅程门控在 welcome 模块收口，任何失败不打断会话创建 ——
        from app.chat.proactive.welcome import send_welcome_if_eligible

        await send_welcome_if_eligible(session, tenant_id, record.id, req.user_id)
        return SessionData(
            session_id=record.id,
            tenant_id=record.tenant_id,
            user_id=record.user_id,
            channel=record.channel,
            status=record.status,
        )

    async def list_history(
        self,
        session: AsyncSession,
        tenant_id: str,
        user_id: str,
        session_id: str,
        limit: int = 20,
        before_id: str | None = None,
    ) -> list[HistoryMessageItem]:
        """历史消息分页（created_at 倒序）。必须校验会话归属，防跨租户/跨用户读取。"""
        chat_session = await chat_session_repository.get_by_id(session, session_id)
        if (
            chat_session is None
            or chat_session.tenant_id != tenant_id
            or chat_session.user_id != user_id
        ):
            # 统一 404，不暴露会话是否存在
            raise AppException(
                message="会话不存在", error_code="SESSION_NOT_FOUND", status_code=404
            )
        messages = await chat_message_repository.list_history_page(
            session, tenant_id, session_id, limit=limit, before_id=before_id
        )
        return [
            HistoryMessageItem(
                message_id=m.id,
                role=m.role,
                content=m.content,
                intent=m.intent,
                status=m.status,
                created_at=m.created_at.isoformat(),
                metadata=m.metadata_json,
            )
            for m in messages
        ]

    async def submit_feedback(
        self, session: AsyncSession, session_id: str, req: FeedbackRequest, tenant_id: str
    ) -> dict:
        """用户反馈落库（Stage 09）。

        归属校验双重：会话属于该租户+用户；message_id 属于该会话且是 AI 回复
        （防跨会话投毒污染回流数据）。重复评价同一消息则更新（幂等）。
        """
        chat_session = await chat_session_repository.get_by_id(session, session_id)
        if (
            chat_session is None
            or chat_session.tenant_id != tenant_id
            or chat_session.user_id != req.user_id
        ):
            raise AppException(
                message="会话不存在", error_code="SESSION_NOT_FOUND", status_code=404
            )
        message = await chat_message_repository.get_by_id(session, req.message_id)
        if (
            message is None
            or message.tenant_id != tenant_id
            or message.session_id != session_id
            or message.role not in ("assistant", "agent")
        ):
            raise AppException(
                message="消息不存在或不可评价", error_code="MESSAGE_NOT_FOUND", status_code=404,
            )

        from app.repositories.chat_feedback_repository import chat_feedback_repository

        existing = await chat_feedback_repository.get_by_message(
            session, tenant_id, req.message_id
        )
        if existing is not None:
            existing.rating = req.rating
            existing.comment = req.comment
            feedback_id = existing.id
        else:
            # SAVEPOINT 包裹：并发对同消息双提交撞唯一索引时不污染主事务，
            # 回查改为更新（幂等语义，连点两下点踩不再 500）
            try:
                async with session.begin_nested():
                    record = await chat_feedback_repository.create(
                        session,
                        tenant_id=tenant_id,
                        session_id=session_id,
                        message_id=req.message_id,
                        rating=req.rating,
                        comment=req.comment,
                    )
                feedback_id = record.id
            except IntegrityError:
                dup = await chat_feedback_repository.get_by_message(
                    session, tenant_id, req.message_id
                )
                if dup is None:
                    raise
                dup.rating = req.rating
                dup.comment = req.comment
                feedback_id = dup.id

        from app.core.metrics import count_feedback

        count_feedback(req.rating)
        return {"feedback_id": feedback_id, "message_id": req.message_id, "rating": req.rating}

    @staticmethod
    async def _log_failed_turn(state: dict, error_code: str, exc: Exception) -> None:
        """失败轮次留痕：用独立 session/事务写一条带 error_json 的 decision_log。

        业务事务已经/即将回滚，决策日志属于可观测数据，不应与业务事务同生共死。
        本方法自身失败只记应用日志，绝不掩盖原始异常。
        注意 error_json 只存异常类型与错误码，不存异常详情（可能含 SQL 参数等敏感内容）。
        """
        try:
            async with AsyncSessionLocal() as log_session:
                log_state = {
                    **state,
                    "status": "FAILED",
                    "error": {"code": error_code, "type": type(exc).__name__},
                }
                await decision_logger.save(log_session, build_log_data(log_state))
                await log_session.commit()
        except Exception:  # noqa: BLE001 - 日志兜底不允许再抛
            logger.exception("failed to persist error decision_log")


# 模块级单例
chat_service = ChatService()
