"""节点：load_session_state —— 读取或初始化会话状态。

职责：
1. 确保 chat_session 存在（开发模式可隐式创建；鉴权模式强制服务端发号）；
2. 校验会话归属：session 已存在时，其 tenant_id / user_id 必须与本次请求一致，
   防止跨租户 / 跨用户续写他人会话、劫持他人进行中的任务（v2 修复的 P0 问题）；
3. 读取 chat_dialog_state，得到当前状态机状态、当前任务与任务栈；
4. 任务时效（Stage 10）：超过 TASK_TTL_MINUTES 未推进的任务加载时丢弃；
5. 加载记忆上下文（Stage 10，MEMORY_PROVIDER 控制）；
6. 语言决策（Stage 19）：请求带的优先，否则会话记忆，否则默认；写入 state.locale。
"""

import copy
import time
from typing import Any

from langchain_core.runnables import RunnableConfig

from app.chat.graph.state import GraphState, get_db_session_from_config
from app.chat.state.types import DialogStateValue, TurnStatus
from app.core.config import settings
from app.core.exceptions import AppException
from app.core.i18n import resolve_locale
from app.repositories.chat_dialog_state_repository import chat_dialog_state_repository
from app.repositories.chat_session_repository import chat_session_repository
from app.repositories.chat_task_repository import chat_task_repository


async def load_session_state(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    """读取或初始化会话与对话状态（读取后提交，归还连接）。"""
    session = get_db_session_from_config(config)
    result = await _load_state(state, session)
    # 阶段边界提交（容量修复）：phase-1 的读与自愈写（隐式建会话/closed 重开/
    # 任务过期标记/locale 记忆）在此定稿，连接随 commit 归还连接池——
    # 中段（意图/槽位/确认门等 LLM 调用）不再持有任何 DB 连接，
    # 下一次 SQL（检索或 save_turn）才重新短暂借出。
    # 并发正确性不变：dialog_state 乐观锁仍在 save_turn 提交时把关（冲突 409）；
    # 归属校验失败等异常路径不经过此处，由上层依赖统一 rollback
    await session.commit()
    return result


async def _load_state(state: GraphState, session: Any) -> dict[str, Any]:
    """load_session_state 的实际逻辑（由包装函数负责提交）。"""
    tenant_id = state["tenant_id"]
    session_id = state["session_id"]

    # 会话存在性：开发模式允许按传入 session_id 隐式创建（联调便利）；
    # 鉴权开启后强制服务端发号（POST /sessions），未创建的会话一律 404，
    # 防调用方伪造/预占任意 id（Stage 08）
    chat_session = await chat_session_repository.get_by_id(session, session_id)
    # 语言决策（Stage 19）：请求带的优先，否则会话记忆，否则默认；
    # 请求显式带 locale 时记入会话 metadata（后续轮不带 locale 就沿用记住的语言）
    req_locale = state.get("locale_req")
    if chat_session is None:
        if settings.AUTH_ENABLED:
            raise AppException(
                message="会话不存在", error_code="SESSION_NOT_FOUND", status_code=404
            )
        locale = resolve_locale(req_locale)
        # 新建会话：带 locale 则一并记入 metadata，供后续轮沿用
        chat_session = await chat_session_repository.create(
            session,
            id=session_id,
            tenant_id=tenant_id,
            user_id=state["user_id"],
            channel=state.get("channel", "web"),
            metadata_json={"locale": locale} if req_locale else None,
        )
    elif chat_session.tenant_id != tenant_id or chat_session.user_id != state["user_id"]:
        # 会话归属校验失败：统一返回 404（不暴露该 session_id 已被占用的信息）
        raise AppException(
            message="会话不存在",
            error_code="SESSION_NOT_FOUND",
            status_code=404,
        )
    else:
        # 既有会话：请求带的优先，否则会话记忆，否则默认；带且变更则更新记忆
        session_meta = dict(chat_session.metadata_json or {})
        locale = (
            resolve_locale(req_locale)
            if req_locale
            else resolve_locale(session_meta.get("locale"))
        )
        if req_locale and session_meta.get("locale") != locale:
            session_meta["locale"] = locale
            chat_session.metadata_json = session_meta

    # —— bot 静默（Stage 07）：人工接管期间用户消息不进决策链 ——
    # 复用 blocked 短路机制（意图分类前拦截，省一次模型推理）；
    # 消息在 save_turn 照常落库（人工要看），回复固定等待话术
    if chat_session is not None and chat_session.status == "handoff":
        return {
            "blocked": True,
            "handoff_silent": True,
            "status": TurnStatus.HANDOFF_SILENT,
            "current_state": DialogStateValue.HANDOFF,
            "locale": locale,
            "graph_trace": ["load_session_state"],
        }

    # —— 会话重开（Stage 15）：closed 会话来新消息自动重开 ——
    # 状态机在关闭时已复位，任务栈不复活（与 TASK_TTL 语义一致）
    if chat_session is not None and chat_session.status == "closed":
        await chat_session_repository.update_status(session, tenant_id, session_id, "active")

    # 读取对话状态机；不存在则视为 IDLE 且无任务
    dialog_state = await chat_dialog_state_repository.get_by_session_id(
        session, tenant_id, session_id
    )

    # —— CSAT 评分捕获（Stage 15）：csat_pending 轮次 + 消息是评分 → 短路 ——
    # 落库/清标记在 save_turn；非评分回复照常进主链路（询问一次性失效）
    context_stacks_loaded: dict[str, Any] = (
        copy.deepcopy(dialog_state.context_stacks_json or {}) if dialog_state is not None else {}
    )
    csat_trigger = context_stacks_loaded.get("csat_pending")
    if csat_trigger:
        from app.chat.csat import parse_csat_score

        score = parse_csat_score(state.get("message", ""))
        if score is not None:
            return {
                "blocked": True,
                "csat_capture": {"score": score, "trigger": str(csat_trigger)},
                "status": TurnStatus.DONE,
                "current_state": dialog_state.state if dialog_state else DialogStateValue.IDLE,
                "context_stacks": context_stacks_loaded,
                "locale": locale,
                "graph_trace": ["load_session_state"],
            }

    if dialog_state is None:
        current_state = DialogStateValue.IDLE
        active_task = None
        task_stack: list = []
    else:
        current_state = dialog_state.state
        # 深拷贝隔离 ORM 的 JSONB 对象引用：下游若原地修改会破坏
        # SQLAlchemy 的变更检测（配合 DialogStateManager 的不可变更新，双保险）
        active_task = copy.deepcopy(dialog_state.active_task_json)
        task_stack = copy.deepcopy(dialog_state.task_stack_json or [])

        # —— 任务时效（Stage 10）：丢弃过期任务，对应 chat_task 行标 ABORTED ——
        ttl_seconds = settings.TASK_TTL_MINUTES * 60
        now = time.time()

        async def _expired(task: dict[str, Any] | None) -> bool:
            if not task:
                return False
            # 旧数据无 updated_ts 视为新鲜（避免升级瞬间误杀进行中任务）
            ts = task.get("updated_ts")
            if ts is None or now - float(ts) <= ttl_seconds:
                return False
            task_id = task.get("task_id")
            if task_id:
                row = await chat_task_repository.get_owned(session, tenant_id, task_id)
                if row is not None and row.status not in ("DONE", "FAILED"):
                    row.status = "ABORTED"
            return True

        if await _expired(active_task):
            active_task = None
            # 任务过期回到 IDLE，避免残留 COLLECTING/CONFIRMING 状态误导后续判定
            if current_state in (DialogStateValue.COLLECTING, DialogStateValue.CONFIRMING):
                current_state = DialogStateValue.IDLE
        task_stack = [item for item in task_stack if not await _expired(item)]

    # —— 记忆上下文（Stage 10）：off 时跳过；失败不影响主链路 ——
    memory: dict[str, Any] | None = None
    if settings.MEMORY_PROVIDER != "off":
        from app.chat.memory.factory import get_memory_provider

        try:
            context = await get_memory_provider().get_context(
                tenant_id, state["user_id"], session_id, state.get("message", "")
            )
            memory = context.to_dict()
        except Exception:  # noqa: BLE001 - 记忆失败不打断主链路
            from app.core.logging import get_logger

            get_logger(__name__).exception("memory get_context failed")

    return {
        "current_state": current_state,
        "active_task": active_task,
        # 本轮开始时的任务快照：active_task 会被状态机覆写，
        # save_turn 依据它判断任务是否在本轮被终结（同步 chat_task 行状态）
        "prev_active_task": copy.deepcopy(active_task),
        "task_stack": task_stack,
        # 会话级上下文栈（UNKNOWN 连击计数、csat_pending 等）
        "context_stacks": context_stacks_loaded,
        "memory": memory,
        "locale": locale,
        "graph_trace": ["load_session_state"],
    }
