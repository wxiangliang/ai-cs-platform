"""LLM 回复润色（Stage 04-01）。

把模板/事实底稿改写为自然客服话术。约束：
- 只润色 DONE / FALLBACK 轮次——补槽（NEEDS_SLOT）、确认门（NEEDS_CONFIRM/CONFIRMED）
  必须保持确定性模板（话术即协议，改写有偏移风险）；
- 事实保护：prompt 强约束数字/金额/订单号/引用原样保留；输出异常（为空、过长、
  丢失底稿中的关键事实）一律回退底稿；
- 未配置 API Key / LLM 失败 → 原样返回底稿，绝不打断主链路。
"""

import re

from app.chat.llm.factory import chat_completion, llm_available
from app.chat.llm.prompts import build_reply_polish_prompt
from app.chat.state.types import TurnStatus
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# 允许润色的单轮状态
_POLISHABLE_STATUS = {TurnStatus.DONE, TurnStatus.FALLBACK}

# 事实指纹：底稿中的数字串（金额/订单号/日期），润色结果必须全部保留
_FACT_RE = re.compile(r"\d+(?:\.\d+)?")


def _facts_preserved(draft: str, polished: str) -> bool:
    """校验底稿中的所有数字事实在润色结果中原样存在。"""
    return all(fact in polished for fact in _FACT_RE.findall(draft))


async def polish_reply(
    draft: str,
    status: str,
    user_message: str,
    memory: dict | None = None,
    soften: bool = False,
    guidelines: str | None = None,
) -> str:
    """润色回复底稿；不满足条件或任何异常时原样返回底稿。

    memory：记忆上下文（Stage 10）——近期轮次进对话历史，
    会话摘要与长期事实供语气/称呼贴合。
    soften：情绪负面轮次（Stage 14 护栏标记）要求先安抚再答。
    """
    if not (
        settings.LLM_REPLY_ENABLED
        and llm_available()
        and status in _POLISHABLE_STATUS
        and draft.strip()
    ):
        return draft

    memory = memory or {}
    system, user = build_reply_polish_prompt(
        draft,
        user_message,
        history=[tuple(t) for t in memory.get("recent_turns", [])],
        session_summary=memory.get("session_summary", ""),
        long_term_facts=memory.get("long_term_facts", []),
        soften=soften,
    )
    # Stage 40 行为准则注入（每轮只注入命中的少数几条，engine 封顶）
    if guidelines:
        system = f"{system}\n\n{guidelines}"
    polished = await chat_completion(system, user, purpose="generate")
    if not polished:
        return draft
    # 输出防线：过长（跑题/扩写）或丢失事实 → 回退底稿（安抚轮放宽一句话的余量）
    slack = 80 if not soften else 140
    if len(polished) > max(len(draft) * 2, len(draft) + slack):
        logger.warning("llm polish output too long, fallback to draft")
        return draft
    if not _facts_preserved(draft, polished):
        logger.warning("llm polish dropped facts, fallback to draft")
        return draft
    # 输出护栏（Stage 14）：提示词泄漏/违禁特征 → 回退底稿
    from app.chat.guardrail.engine import guardrail_engine
    from app.core.metrics import count_guardrail_block

    violated = guardrail_engine.check_output(polished)
    if violated:
        logger.warning("llm polish output guardrail hit: rule=%s, fallback to draft", violated)
        count_guardrail_block(violated)
        return draft
    return polished
