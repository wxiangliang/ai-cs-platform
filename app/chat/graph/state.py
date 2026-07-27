"""LangGraph 聊天主链路状态定义。

GraphState 在各节点间流转。节点返回的字典会按字段合并进 state：
- graph_trace 使用 add reducer 累加各节点名（记录执行轨迹）；
- 其余字段默认覆盖式合并。

运行期依赖（db_session）不放 state：state 必须全部可序列化（为 checkpointer 铺路），
依赖通过 LangGraph config 注入（见 utils.get_db_session_from_config）。
"""

import operator
from typing import Annotated, Any, TypedDict

from langchain_core.runnables import RunnableConfig
from sqlalchemy.ext.asyncio import AsyncSession


def get_db_session_from_config(config: RunnableConfig) -> AsyncSession:
    """从 LangGraph config 取本次请求的数据库会话（ChatService 注入）。"""
    return config["configurable"]["db_session"]  # type: ignore[index,no-any-return]


class GraphState(TypedDict, total=False):
    """聊天主链路图状态。total=False 允许节点只返回部分字段。"""

    # —— 输入 ——
    tenant_id: str
    session_id: str
    user_id: str
    channel: str
    # 用户语言（Stage 19：模板查表/prompt 注入都用它，默认 LOCALE_DEFAULT）
    locale: str
    # 请求原始 locale（可能为 None）：load_session_state 据它结合会话记忆决策
    locale_req: str | None
    message: str
    trace_id: str

    # —— 计时起点 ——
    start_ts: float

    # —— 预处理 / 护栏 ——
    normalized_text: str
    blocked: bool
    guardrail: dict[str, Any]
    # 人工接管期间的静默轮次（Stage 07）：blocked=True + 本标志，
    # 消息照常落库但不进决策链，回复固定等待话术
    handoff_silent: bool
    # 会话级上下文栈（UNKNOWN 连击计数等，随 dialog_state 持久化）
    context_stacks: dict[str, Any]

    # —— 决策中间结果 ——
    intent_result: dict[str, Any]
    slots: dict[str, Any]
    current_state: str
    active_task: dict[str, Any] | None
    new_state: str
    status: str
    selected_skill: str | None
    missing_slot: str | None
    # —— Stage 10：多意图 ——
    # 主意图段文本（槽位抽取范围，防串槽）；次要意图 [{intent, slots}] 待入任务栈
    slot_text: str
    pending_intents: list[dict[str, Any]]
    # 任务因追问超限被放弃（回复给转人工建议话术）
    task_gave_up: bool
    # 任务被中途否定（Stage 23：回复重定向话术，{intent: 被否定任务意图}）
    denied_task: dict[str, Any] | None
    # L3 弱确认降级重确认（Stage 13：回复前加提示语）
    weak_confirm_recheck: bool
    # 护栏拦截话术（Stage 14：response_generate 优先使用）
    guardrail_reply: str
    # 轻度情绪标记（Stage 14：negative 时回复润色放软话术）
    emotion: str
    # CSAT 评分捕获（Stage 15：{score, trigger}，落库在 save_turn）
    csat_capture: dict[str, Any]
    # 本轮记忆上下文 {session_summary, recent_turns, long_term_facts}
    memory: dict[str, Any] | None

    # —— Stage 05：任务栈与执行交接 ——
    task_stack: list[dict[str, Any]]
    finished_task: dict[str, Any] | None  # 确认通过、待 ActionExecutor 执行的任务
    resumed_task: dict[str, Any] | None  # 本轮从栈恢复的任务（回复附续办提示）
    prev_active_task: dict[str, Any] | None  # 本轮开始时的任务快照（chat_task 行同步用）
    # 本轮最终生效的累计槽位（含任务历史收集的），供回复模板渲染。
    # 任务结束（CONFIRMED/DONE）时 active_task 已清空，模板槽位只能从这里取
    effective_slots: dict[str, Any]
    reply: str

    # —— RAG（Stage 06）——
    # 检索过程记录（落 decision_log.retrieval_json）
    retrieval: dict[str, Any] | None
    # 回答来源：faq / rag_llm / rag_extract / refused
    answer_source: str | None

    # —— A/B 实验（Stage 18）——
    # 本轮命中的实验变体分配 + 参数覆盖（落 decision_log.experiment_json；无实验时 None）
    experiment: dict[str, Any] | None

    # —— 落库 / 输出 ——
    user_message_id: str
    ai_message_id: str
    error: dict[str, Any] | None

    # 执行轨迹：每个节点 append 自己的名字
    graph_trace: Annotated[list[str], operator.add]
    # 各阶段耗时
    latency: dict[str, Any]
