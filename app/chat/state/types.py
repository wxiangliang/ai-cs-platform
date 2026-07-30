"""对话状态机类型定义。"""

from dataclasses import dataclass, field
from typing import Any


class DialogStateValue:
    """第一版状态机状态。"""

    IDLE = "IDLE"  # 无任务
    COLLECTING = "COLLECTING"  # 正在补槽
    CONFIRMING = "CONFIRMING"  # 等用户确认（写操作确认门）
    DONE = "DONE"  # 完成
    ABORTED = "ABORTED"  # 用户取消
    HANDOFF = "HANDOFF"  # 转人工
    FAILED = "FAILED"  # 失败


class TurnStatus:
    """单轮处理状态（用于消息与 decision_log 的 status 字段，以及 API 返回）。"""

    NEEDS_SLOT = "NEEDS_SLOT"  # 缺槽位，需追问
    NEEDS_CONFIRM = "NEEDS_CONFIRM"  # 需用户确认（写操作）
    CONFIRMED = "CONFIRMED"  # 用户已确认，写操作已受理（Stage 03 只受理，Stage 05 才真正执行）
    DONE = "DONE"  # 本轮完成
    HANDOFF = "HANDOFF"  # 转人工
    HANDOFF_SILENT = "HANDOFF_SILENT"  # 人工接管期间 bot 静默轮次（Stage 07）
    ABORTED = "ABORTED"  # 已取消
    FALLBACK = "FALLBACK"  # 兜底未知
    FAILED = "FAILED"  # 处理失败（如护栏拦截）


@dataclass
class ResolveResult:
    """状态机流转结果。

    new_state：流转后的状态机状态；
    status：本轮处理状态；
    active_task：流转后的当前任务（None 表示无任务 / 已清空）；
    missing_slot：缺失的槽位名（用于追问），无则为 None；
    intent：最终生效的意图（SLOT_ONLY 续接时会变成原任务意图）；
    task_stack：流转后的挂起任务栈（Stage 05）；
    finished_task：本轮确认通过、待 ActionExecutor 执行的任务（Stage 05）；
    resumed_task：本轮从栈中恢复的任务（回复需附带续办提示，Stage 05）。
    """

    new_state: str
    status: str
    active_task: dict[str, Any] | None = None
    missing_slot: str | None = None
    intent: str | None = None
    collected_slots: dict[str, Any] = field(default_factory=dict)
    task_stack: list[dict[str, Any]] = field(default_factory=list)
    finished_task: dict[str, Any] | None = None
    resumed_task: dict[str, Any] | None = None
    # 任务因追问超限被放弃（回复给转人工建议话术，Stage 10）
    gave_up: bool = False
    # 任务被用户中途否定（Stage 23 方向纠偏：回复重定向话术，只终止当前任务）
    denied_task: dict[str, Any] | None = None
    # 切换守护拦截（Stage 26）：任务进行中新意图证据不足，保留当前任务，
    # 值为被拦截的候选新意图码（回复渲染二选一澄清话术）
    switch_candidate: str | None = None
    # UNKNOWN 续接收紧（Stage 26）：有任务但本轮既非槽位应答也无续接证据，
    # 不填槽不切换，回复改二选一澄清而非原话重问
    unknown_with_task: bool = False
