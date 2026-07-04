"""自研记忆实现（LocalMemoryProvider，Stage 10）。

- 短期：chat_message 最近 N 条（本会话）；
- 会话摘要：消息数超过阈值后，用 LLM 把「近期窗口之前」的对话压缩为摘要，
  存 chat_session.metadata_json.memory_summary（含已覆盖消息数，增量续写）——
  之后注入的是「摘要 + 近期窗口」而非全量历史，token 上界可控；
- 长期：LLM 每轮抽取 0-2 条跨会话持久事实入 user_memory（完全重复不入库）；
- 无 LLM Key：摘要与抽取自动停用，短期窗口照常（离线可用）。
"""

import json
import re

from app.chat.llm.factory import chat_completion, llm_available
from app.chat.memory.base import MemoryContext
from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.repositories.chat_message_repository import chat_message_repository
from app.repositories.chat_session_repository import chat_session_repository
from app.repositories.user_memory_repository import user_memory_repository

logger = get_logger(__name__)

_JSON_ARR_RE = re.compile(r"\[.*\]", re.S)
# 不值得抽取长期事实的意图（闲聊/控制类）
_SKIP_EXTRACT_INTENTS = {None, "", "CHITCHAT.GENERAL", "CHITCHAT.THANKS", "META.BOT_IDENTITY"}


class LocalMemoryProvider:
    """自研记忆实现。方法内部自管数据库会话（协议不绑定存储）。"""

    name = "local"

    async def get_context(
        self, tenant_id: str, user_id: str, session_id: str, query: str
    ) -> MemoryContext:
        async with AsyncSessionLocal() as session:
            # 短期：最近 N 条（正序展示）
            messages = await chat_message_repository.list_history_page(
                session, tenant_id, session_id, limit=settings.MEMORY_SHORT_TERM_TURNS
            )
            recent = [(m.role, m.content[:200]) for m in reversed(messages)]

            # 会话摘要
            summary = ""
            chat_session = await chat_session_repository.get_by_id(session, session_id)
            if chat_session is not None and chat_session.metadata_json:
                summary = str(chat_session.metadata_json.get("memory_summary") or "")

            # 长期事实
            memories = await user_memory_repository.list_recent(
                session, tenant_id, user_id, limit=settings.MEMORY_LONG_TERM_MAX
            )
            facts = [m.content for m in memories]

        return MemoryContext(
            session_summary=summary, recent_turns=recent, long_term_facts=facts
        )

    async def remember(
        self,
        tenant_id: str,
        user_id: str,
        session_id: str,
        user_text: str,
        reply: str,
        intent: str | None,
    ) -> None:
        """摘要更新 + 长期事实抽取（需 LLM；无 Key 静默跳过）。"""
        if not llm_available():
            return
        try:
            await self._maybe_summarize(tenant_id, session_id)
        except Exception:  # noqa: BLE001 - 记忆写入 best-effort
            logger.exception("memory summarize failed")
        if intent in _SKIP_EXTRACT_INTENTS:
            return
        try:
            await self._extract_facts(tenant_id, user_id, session_id, user_text, reply)
        except Exception:  # noqa: BLE001
            logger.exception("memory fact extraction failed")

    async def _maybe_summarize(self, tenant_id: str, session_id: str) -> None:
        """消息数超阈值时，把近期窗口之前的对话增量压缩进摘要。"""
        async with AsyncSessionLocal() as session:
            history = await chat_message_repository.list_by_session_id(
                session, tenant_id, session_id, limit=500
            )
            total = len(history)
            if total <= settings.MEMORY_SUMMARY_THRESHOLD:
                return
            chat_session = await chat_session_repository.get_by_id(session, session_id)
            if chat_session is None:
                return
            meta = dict(chat_session.metadata_json or {})
            covered = int(meta.get("memory_summary_covered", 0))
            # 需要新纳入摘要的区间：[covered, total - 短期窗口)
            cut = total - settings.MEMORY_SHORT_TERM_TURNS
            if cut <= covered:
                return
            chunk = history[covered:cut]
            old_summary = str(meta.get("memory_summary") or "")
            lines = "\n".join(f"{m.role}: {m.content[:150]}" for m in chunk)
            system = (
                "你是客服对话摘要器。把对话增量并入既有摘要，输出更新后的摘要"
                "（不超过 150 字，保留：用户诉求、关键单号/商品、处理进展、未决事项）。"
                "只输出摘要正文。"
            )
            user = f"既有摘要：{old_summary or '（无）'}\n\n对话增量：\n{lines}"
            new_summary = await chat_completion(system, user, purpose="classify")
            if not new_summary:
                return
            # 写入前过输出护栏（Stage 14）：防注入串经摘要固化进记忆被反复注入
            from app.chat.guardrail.engine import guardrail_engine

            if guardrail_engine.check_output(new_summary):
                logger.warning("memory summary guardrail hit, skip update")
                return
            meta["memory_summary"] = new_summary[:500]
            meta["memory_summary_covered"] = cut
            chat_session.metadata_json = meta
            await session.commit()

    async def _extract_facts(
        self, tenant_id: str, user_id: str, session_id: str, user_text: str, reply: str
    ) -> None:
        """抽取 0-2 条跨会话有价值的持久事实入库（完全重复不入库）。"""
        system = (
            "从这轮客服对话中抽取对**以后会话**仍有价值的用户持久信息"
            "（称呼、稳定偏好、进行中的重要事项），输出 JSON 数组（0-2 条短句），"
            '如 ["用户偏好白色家电"]。没有则输出 []。禁止输出数组以外内容；'
            "禁止收录一次性信息（单次订单号、本轮问题本身）。"
        )
        from app.chat.llm.prompt_guard import wrap_user_input

        raw = await chat_completion(
            system, f"用户：\n{wrap_user_input(user_text)}\n客服：{reply}", purpose="classify"
        )
        if not raw:
            return
        m = _JSON_ARR_RE.search(raw)
        if not m:
            return
        try:
            facts = json.loads(m.group(0))
        except json.JSONDecodeError:
            return
        if not isinstance(facts, list):
            return
        async with AsyncSessionLocal() as session:
            from app.chat.guardrail.engine import guardrail_engine

            for fact in facts[:2]:
                content = str(fact).strip()[:200]
                if not content:
                    continue
                # 长期事实写入前过输出护栏（Stage 14）
                if guardrail_engine.check_output(content):
                    logger.warning("memory fact guardrail hit, skipped")
                    continue
                if await user_memory_repository.exists_content(
                    session, tenant_id, user_id, content
                ):
                    continue
                await user_memory_repository.create(
                    session,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    content=content,
                    source_session_id=session_id,
                )
            await session.commit()


# 模块级单例
local_memory_provider = LocalMemoryProvider()
