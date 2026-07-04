"""记忆协议与上下文类型（Stage 10）。

短期 = 本会话近期窗口 + 会话摘要（防上下文膨胀，token 上界可控）；
长期 = 跨会话的用户持久事实。消费方是 LLM 提示词（回复润色 / RAG 生成）。
remember 为每轮结束后的异步 best-effort 调用，失败绝不影响主链路。
"""

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class MemoryContext:
    """一次请求可用的记忆上下文。"""

    session_summary: str = ""  # 本会话较早对话的摘要
    recent_turns: list[tuple[str, str]] = field(default_factory=list)  # [(role, text)]
    long_term_facts: list[str] = field(default_factory=list)  # 跨会话持久事实

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_summary": self.session_summary,
            "recent_turns": self.recent_turns,
            "long_term_facts": self.long_term_facts,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "MemoryContext":
        data = data or {}
        return cls(
            session_summary=data.get("session_summary", ""),
            recent_turns=[tuple(t) for t in data.get("recent_turns", [])],
            long_term_facts=list(data.get("long_term_facts", [])),
        )


class MemoryProvider(Protocol):
    """记忆提供方协议。"""

    name: str

    async def get_context(
        self, tenant_id: str, user_id: str, session_id: str, query: str
    ) -> MemoryContext:
        """取本轮可注入的记忆上下文（query 供长期记忆相关性检索）。"""
        ...

    async def remember(
        self,
        tenant_id: str,
        user_id: str,
        session_id: str,
        user_text: str,
        reply: str,
        intent: str | None,
    ) -> None:
        """记录一轮对话（异步 best-effort：摘要更新 / 长期事实抽取）。"""
        ...
