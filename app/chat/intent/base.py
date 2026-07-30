"""意图分类器抽象接口。

定义统一的 IntentClassifier 协议，规则实现（Stage 03）与
混合 LLM 实现（Stage 04，见 docs/requirements/stage-04-llm-integration/）都遵守它，
图节点只依赖协议，不依赖具体实现，便于按配置切换。

接口是 async 的：规则实现内部无 IO 也包装为 async，
避免 Stage 04 引入 LLM 时再改节点签名。
"""

from typing import Any, Protocol

from app.chat.intent.types import IntentResult


class IntentClassifier(Protocol):
    """意图分类器协议。

    classify 是上下文敏感的：META.CONFIRM / META.DENY 仅当
    current_state == CONFIRMING 时允许输出；META.SLOT_ONLY 的续接
    依赖 has_active_task（详见 docs/chat/intent_taxonomy.md 第 4 节）。
    Stage 26 起补槽守护同样上下文敏感：COLLECTING 下传入 pending_slot
    时优先做定向提取（「订单号是…」直接续接，不进语义层）。
    """

    async def classify(
        self,
        text: str,
        *,
        current_state: str = "IDLE",
        has_active_task: bool = False,
        pending_slot: str | None = None,
        collected_slots: dict[str, Any] | None = None,
    ) -> IntentResult:
        """对归一化文本做意图识别，返回 IntentResult。"""
        ...
