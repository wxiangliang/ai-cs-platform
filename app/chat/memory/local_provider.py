"""自研记忆实现（LocalMemoryProvider，Stage 10；Stage 20 摘要结构化）。

- 短期：chat_message 最近 N 条（本会话）；
- 会话摘要：消息数超过阈值后，用 LLM 把「近期窗口之前」的对话压缩为**结构化摘要**
  （固定 schema JSON：诉求/涉及实体/进展/待办/已答复，Stage 20），
  存 chat_session.metadata_json.memory_summary（含已覆盖消息数，增量续写）——
  之后注入的是「摘要 + 近期窗口」而非全量历史，token 上界可控；
  LLM 输出非法 JSON 时降级为纯文本摘要存储，存量纯文本摘要（Stage 10 旧格式）兼容可读；
- 长期：LLM 每轮抽取 0-2 条跨会话持久事实入 user_memory（完全重复不入库）；
- 无 LLM Key：摘要与抽取自动停用，短期窗口照常（离线可用）。
"""

import json
import re
from typing import Any

from app.chat.llm.factory import chat_completion, llm_available
from app.chat.llm.prompt_guard import wrap_user_input
from app.chat.memory.base import MemoryContext
from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.repositories.chat_message_repository import chat_message_repository
from app.repositories.chat_session_repository import chat_session_repository
from app.repositories.user_memory_repository import user_memory_repository

logger = get_logger(__name__)

_JSON_ARR_RE = re.compile(r"\[.*\]", re.S)
_JSON_OBJ_RE = re.compile(r"\{.*\}", re.S)
# 不值得抽取长期事实的意图（闲聊/控制类）
_SKIP_EXTRACT_INTENTS = {None, "", "CHITCHAT.GENERAL", "CHITCHAT.THANKS", "META.BOT_IDENTITY"}

# ---- 结构化摘要（Stage 20）：字段与编译时上界，不靠 LLM 自觉 ----
_SUMMARY_TEXT_FIELDS = ("request", "progress")  # 单值文本字段
_SUMMARY_LIST_FIELDS = ("entities", "pending", "answered")  # 数组字段
_SUMMARY_LIST_MAX = 5  # 数组各限 5 条，超出丢最旧
_SUMMARY_MAX_CHARS = 500  # JSON 序列化总长上限，超出拒写保留旧摘要

_SUMMARY_SYSTEM = (
    "你是客服对话摘要器。把「对话增量」并入「既有摘要」，输出更新后的摘要。\n"
    "只输出一个 JSON 对象，不要输出任何其他内容，schema：\n"
    '{"request": "用户核心诉求（一句话）", '
    '"entities": ["涉及的具体订单号/物流单号/商品名/金额"], '
    '"progress": "处理进展（一句话）", '
    '"pending": ["未决事项"], "answered": ["已答复事项（避免重复答）"]}\n'
    "硬约束：\n"
    "1. 具体单号、金额、商品名必须保留**原文**，禁止概括泛化（如「某订单」「相关商品」）；\n"
    "2. entities/pending/answered 各不超过 5 条，保留最新；没有内容的字段给空字符串或空数组；\n"
    "3. 整个 JSON 不超过 500 字。"
)


def _render_summary(summary: Any) -> str:
    """结构化摘要 dict → 紧凑单行中文；纯文本（存量旧格式）原样返回。

    对外接口零改动的关键：MemoryContext.session_summary 始终是 str，
    下游（润色/RAG 生成）不感知 JSON 结构。
    """
    if not isinstance(summary, dict):
        return str(summary or "")
    parts: list[str] = []
    if summary.get("request"):
        parts.append(f"诉求：{summary['request']}")
    if summary.get("entities"):
        parts.append("涉及：" + "、".join(summary["entities"]))
    if summary.get("progress"):
        parts.append(f"进展：{summary['progress']}")
    if summary.get("pending"):
        parts.append("待办：" + "、".join(summary["pending"]))
    if summary.get("answered"):
        parts.append("已答复：" + "、".join(summary["answered"]))
    return "；".join(parts)


def _parse_summary(raw: str) -> dict[str, Any] | None:
    """从 LLM 输出中提取摘要 JSON 对象；非法/非 dict 返回 None（走纯文本降级）。"""
    m = _JSON_OBJ_RE.search(raw)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _normalize_summary(data: dict[str, Any]) -> dict[str, Any]:
    """裁剪结构化摘要到编译时上界：只留声明字段、数组各限 5 条（丢最旧）。"""
    out: dict[str, Any] = {}
    for field in _SUMMARY_TEXT_FIELDS:
        value = data.get(field)
        if isinstance(value, str) and value.strip():
            out[field] = value.strip()
    for field in _SUMMARY_LIST_FIELDS:
        value = data.get(field)
        if isinstance(value, list):
            items = [str(v).strip() for v in value if str(v).strip()]
            if items:
                out[field] = items[-_SUMMARY_LIST_MAX:]
    return out


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

            # 会话摘要：结构化 dict（Stage 20）渲染为紧凑字符串；存量纯文本原样返回
            summary = ""
            chat_session = await chat_session_repository.get_by_id(session, session_id)
            if chat_session is not None and chat_session.metadata_json:
                summary = _render_summary(chat_session.metadata_json.get("memory_summary"))

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
        """消息数超阈值时，把近期窗口之前的对话增量压缩进结构化摘要（Stage 20）。

        单一表示不变式：摘要覆盖区间 [0, cut) 与短期窗口 [total - 窗口, total) 互斥
        （cut = total - MEMORY_SHORT_TERM_TURNS）——同一条消息**禁止**同时以
        「摘要」和「窗口原文」两种形态进入同一个 prompt。改窗口/阈值参数不得打破
        （tests/stage20 有回归测试锁定）。
        """
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
            # 既有摘要可能是结构化 dict（新）或纯文本（存量），统一转文本给 LLM 续写
            old_raw = meta.get("memory_summary")
            old_text = (
                json.dumps(old_raw, ensure_ascii=False)
                if isinstance(old_raw, dict)
                else str(old_raw or "")
            )
            lines = "\n".join(f"{m.role}: {m.content[:150]}" for m in chunk)
            # 对话增量含用户原文，经防注入包裹（Stage 14）：数据而非指令
            user = f"既有摘要：{old_text or '（无）'}\n\n对话增量：\n{wrap_user_input(lines)}"
            new_summary = await chat_completion(_SUMMARY_SYSTEM, user, purpose="classify")
            if not new_summary:
                return
            from app.chat.guardrail.engine import guardrail_engine

            parsed = _parse_summary(new_summary)
            if parsed is not None and (normalized := _normalize_summary(parsed)):
                # 结构化路径：裁剪后仍超总长上界 → 拒写保留旧摘要（covered 不推进，下轮重试）
                if len(json.dumps(normalized, ensure_ascii=False)) > _SUMMARY_MAX_CHARS:
                    logger.warning("structured summary over budget, keep old summary")
                    return
                # 写入前过输出护栏（Stage 14）：防注入串经摘要固化进记忆被反复注入
                if guardrail_engine.check_output(_render_summary(normalized)):
                    logger.warning("memory summary guardrail hit, skip update")
                    return
                meta["memory_summary"] = normalized
                meta["summary_format"] = "json"
            else:
                # 降级路径：LLM 输出不是合法 JSON → 按旧版纯文本原样存储，不重试不报错
                text = new_summary.strip()[:_SUMMARY_MAX_CHARS]
                if guardrail_engine.check_output(text):
                    logger.warning("memory summary guardrail hit, skip update")
                    return
                meta["memory_summary"] = text
                meta["summary_format"] = "text"
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
