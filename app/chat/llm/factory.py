"""LLM Provider 工厂（Stage 04-01）。

统一封装模型调用（OpenAI 兼容接口，OPENAI_BASE_URL 可指向本地推理服务）：
- 强制 timeout 与有限重试；温度按用途区分（分类 0 / 生成 0.7）；
- 所有配置走 settings，日志禁止输出 API Key；
- 未配置 Key 时 llm_available() 为 False，各调用方自行降级（模板/规则），
  LLM 永远是增强层，不是主链路的硬依赖。
"""

import asyncio
from functools import lru_cache
from typing import Any, Literal

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

Purpose = Literal["classify", "generate", "rag"]

# 按用途区分的采样温度：分类要确定性，生成要一点自然度，
# RAG 生成偏保守（忠于检索资料，少发挥）
_TEMPERATURE: dict[str, float] = {"classify": 0.0, "generate": 0.7, "rag": 0.3}


def llm_available() -> bool:
    """LLM 是否可用（未配置 API Key 即不可用，调用方走降级路径）。"""
    return bool(settings.OPENAI_API_KEY)


def _model_for_purpose(purpose: Purpose) -> str:
    """按用途选模型 tier（Stage 17 分级路由）。

    classify（意图二判/槽位抽取/短润色）走 fast 小模型；
    generate（润色）/ rag（知识生成，属难例）走 smart 大模型。
    未配置对应 tier 时回落到 CHAT_MODEL，行为不变（零回归）。
    """
    if purpose == "classify":
        return settings.CHAT_MODEL_FAST or settings.CHAT_MODEL_SMART or settings.CHAT_MODEL
    return settings.CHAT_MODEL_SMART or settings.CHAT_MODEL


@lru_cache(maxsize=4)
def get_chat_model(purpose: Purpose = "generate") -> Any:
    """按用途获取 ChatModel（进程内按用途缓存）。

    调用方必须先检查 llm_available()；本函数不做二次校验，
    以便测试中用 fake key + monkeypatch 客户端。
    """
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=_model_for_purpose(purpose),
        api_key=settings.OPENAI_API_KEY,  # type: ignore[arg-type]
        base_url=settings.OPENAI_BASE_URL,
        temperature=_TEMPERATURE[purpose],
        timeout=settings.LLM_TIMEOUT,
        max_retries=1,
    )


async def chat_completion(system: str, user: str, purpose: Purpose = "generate") -> str | None:
    """一次对话补全：返回文本，任何异常返回 None（调用方降级）。

    统一收口 LLM 调用的异常与日志，各增强点不重复写 try/except。
    """
    if not llm_available():
        return None
    from app.chat.llm.budget import account_llm_result, get_current_tenant, is_over_budget
    from app.chat.llm.deadline import budget_exhausted, remaining_budget
    from app.core.metrics import count_llm_budget_exceeded, count_llm_call

    # 预算熔断（Stage 17）：当前租户当日超预算 → 与无 Key 同路径降级到模板/规则
    tenant = get_current_tenant()
    if await is_over_budget(tenant):
        count_llm_budget_exceeded(tenant)
        return None
    # 轮级时间预算（容量修复）：本轮 LLM 时间已用尽 → 直接降级，不再排队等超时
    if budget_exhausted():
        logger.warning("turn llm budget exhausted, degrade (purpose=%s)", purpose)
        return None

    try:
        from langchain_core.messages import HumanMessage, SystemMessage

        # Langfuse 追踪（Stage 12）：图内调用经 OTel 上下文归入本轮 trace，
        # 图外调用（记忆摘要等）自成 trace；未启用时 handler 为 None 零开销
        from app.core.tracing import get_langfuse_handler

        handler = get_langfuse_handler()
        invoke_config: dict[str, Any] = {"run_name": f"llm_{purpose}"}
        if handler is not None:
            invoke_config["callbacks"] = [handler]

        model = get_chat_model(purpose)
        # 单次调用以剩余轮预算为外层超时：客户端内部 timeout×重试可能远超剩余预算，
        # wait_for 兜底后走 except 降级（TimeoutError 同样落 ok=False）
        coro = model.ainvoke(
            [SystemMessage(content=system), HumanMessage(content=user)],
            config=invoke_config,  # type: ignore[arg-type]
        )
        remaining = remaining_budget()
        result = await (
            asyncio.wait_for(coro, timeout=remaining) if remaining is not None else coro
        )
        text = result.content if isinstance(result.content, str) else ""
        count_llm_call(purpose, ok=True)
        # usage 计量与预算累计（Stage 17）：best-effort，取不到 token 静默跳过
        await account_llm_result(purpose, result)
        return text.strip() or None
    except Exception:  # noqa: BLE001 - LLM 故障降级，不打断主链路
        logger.exception("llm chat_completion failed (purpose=%s)", purpose)
        count_llm_call(purpose, ok=False)
        return None
