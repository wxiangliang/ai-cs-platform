"""节点写契约（post-stage-27 工程护栏 ①，借鉴 Dify 变量声明纪律）。

GraphState 是全局共享的：任何节点技术上都能写任意键，字段边界全靠注释
自觉——本模块把「每个节点允许写哪些字段」登记成表，并在图构建时包装
节点执法。目的不是限制今天的代码（契约按现状如实登记，零行为变更），
而是防未来腐化：第 50 次改动想让节点偷偷多写一个字段时，任何一条跑过
该节点的既有测试都会当场红。

执法纪律：
- dev/test 环境违约直接抛 GraphContractViolation（fail fast）；
- prod 只告警放行——契约保护开发过程，绝不为契约 bug 打断用户会话
  （与「生产硬门禁在启动期、运行期宁降级不中断」的既有纪律一致）；
- graph_trace 是 add reducer（人人追加），不进契约。

维护约定：节点要新写字段，必须同步本表——diff 里的这一行就是
「该节点职责扩大」的显式信号，review 时有意识地过一遍。
契约完整性由 tests/graph/test_write_contracts.py 静态锁定
（注册节点 == 契约键集合、契约字段 ⊆ GraphState 声明）。
"""

import functools
from collections.abc import Awaitable, Callable
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# 各节点允许写入的 GraphState 字段（2026-08-04 按 AST 审计的现状如实登记）
NODE_WRITES: dict[str, frozenset[str]] = {
    "load_session_state": frozenset(
        {
            "current_state", "active_task", "task_stack", "prev_active_task",
            "context_stacks", "blocked", "handoff_silent", "csat_capture",
            "locale", "memory", "status",
        }
    ),
    "preprocess_message": frozenset({"normalized_text"}),
    "guardrail_check": frozenset(
        {"blocked", "guardrail", "guardrail_reply", "status", "emotion"}
    ),
    "intent_classify": frozenset({"intent_result", "slot_text", "pending_intents"}),
    "slot_extract": frozenset({"slots"}),
    "confirmation_parse": frozenset({"intent_result", "weak_confirm_recheck", "slots"}),
    "dialog_state_resolve": frozenset(
        {
            "new_state", "status", "active_task", "missing_slot", "task_stack",
            "finished_task", "resumed_task", "task_gave_up", "denied_task",
            "switch_candidate", "unknown_with_task", "effective_slots",
            "intent_result", "meta_shadow",
        }
    ),
    "skill_resolve": frozenset({"selected_skill"}),
    "response_generate": frozenset({"reply"}),
    "rag_answer": frozenset({"reply", "retrieval", "answer_source"}),
    "product_answer": frozenset({"reply", "retrieval", "answer_source"}),
    "tool_invoke": frozenset({"reply", "retrieval", "answer_source"}),
    "action_execute": frozenset({"reply", "retrieval", "answer_source"}),
    # save_turn 的 reply：续办提示/CSAT 询问等对最终回复的追加（Stage 05/15）
    "save_turn": frozenset(
        {"user_message_id", "ai_message_id", "reply", "latency"}
    ),
}

# add reducer 字段：所有节点都追加执行轨迹，不进契约
_REDUCER_FIELDS = frozenset({"graph_trace"})

NodeFn = Callable[..., Awaitable[dict[str, Any]]]


class GraphContractViolation(RuntimeError):
    """节点写入了契约未声明的 GraphState 字段。"""


def enforce_write_contract(name: str, fn: NodeFn) -> NodeFn:
    """包装节点：返回键必须 ⊆ 契约声明。

    prod 只告警（不为契约 bug 打断会话）；其余环境直接抛错让测试红。
    """
    allowed = NODE_WRITES[name] | _REDUCER_FIELDS

    # functools.wraps 保留原签名（经 __wrapped__）：LangGraph 按签名决定
    # 是否给节点传 config（save_turn 等 DB 节点依赖），包装不得掩盖它
    @functools.wraps(fn)
    async def wrapped(state: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
        result = await fn(state, *args, **kwargs)
        illegal = set(result) - allowed
        if illegal:
            message = f"节点 {name} 写入未声明字段: {sorted(illegal)}（契约见 contracts.py）"
            # 生产类环境（staging/prod，口径同 config._PROD_ENVS）只告警放行
            from app.core.config import _PROD_ENVS

            if settings.APP_ENV.lower() in _PROD_ENVS:
                logger.warning(message)
            else:
                raise GraphContractViolation(message)
        return result

    return wrapped
