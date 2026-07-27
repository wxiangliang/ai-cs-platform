"""Stage 17 租户 token 预算与熔断。

LLM usage 在 factory（及 RAG 生成）统一收口累计；本模块提供：
- 当前请求租户上下文（contextvar，入口 set，供 usage 归属与熔断判定）；
- Redis 按日累计 token；
- 是否超预算判定。

红线：预算/后端故障一律 **fail-open**（不阻断 LLM），只有「确实累计已达阈值」
才熔断——此时 LLM 增强层降级到模板，与「无 API Key」同路径，主链路照常出答案。
"""

from __future__ import annotations

from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# 当前请求的租户 id：chat_service 入口 set_current_tenant 写入，
# 背景任务（记忆摘要）由 asyncio.create_task 复制上下文自动继承
_tenant_ctx: ContextVar[str] = ContextVar("llm_budget_tenant", default="")

# 首次写入的日 key 过期（秒）：跨天自动清理，留 2 天冗余
_DAY_TTL_SECONDS = 172800


def set_current_tenant(tenant_id: str) -> None:
    """写入当前上下文的租户 id（预算归属用）。"""
    _tenant_ctx.set(tenant_id or "")


def get_current_tenant() -> str:
    """读取当前上下文的租户 id，未设置返回空串。"""
    return _tenant_ctx.get()


def _day_key(tenant_id: str) -> str:
    """按 UTC 日期分桶的 Redis key。"""
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"llm:budget:{tenant_id}:{day}"


async def is_over_budget(tenant_id: str) -> bool:
    """当前租户当日 token 是否已达日预算。

    未开启 / 无阈值(≤0，即不限) / 无租户 / 后端故障 → False（放行，fail-open）。
    """
    if not settings.LLM_BUDGET_ENABLED or settings.LLM_BUDGET_DAILY_TOKENS <= 0:
        return False
    if not tenant_id:
        return False
    try:
        from app.cache.redis_client import get_redis_client

        raw = await get_redis_client().get(_day_key(tenant_id))
        used = int(raw) if raw else 0
        return used >= settings.LLM_BUDGET_DAILY_TOKENS
    except Exception:  # noqa: BLE001 - 预算后端故障 fail-open，不阻断 LLM
        logger.warning("llm budget check failed, fail-open", exc_info=True)
        return False


async def record_usage(tenant_id: str, tokens: int) -> None:
    """累计当日 token 用量（Redis INCRBY，首次写入设过期）。故障静默。"""
    if not settings.LLM_BUDGET_ENABLED or tokens <= 0 or not tenant_id:
        return
    try:
        from app.cache.redis_client import get_redis_client

        r = get_redis_client()
        key = _day_key(tenant_id)
        new_total = await r.incrby(key, tokens)
        if new_total == tokens:
            # 首次写入才设过期，避免每次刷新过期时间导致永不清理
            await r.expire(key, _DAY_TTL_SECONDS)
    except Exception:  # noqa: BLE001 - 计量故障不影响主链路
        logger.warning("llm budget record failed", exc_info=True)


def extract_token_usage(result: Any) -> int:
    """从 LangChain 返回消息里取本次调用总 token 数（取不到返回 0）。

    provider 差异：优先 usage_metadata（标准结构），回落 response_metadata.token_usage。
    """
    usage = getattr(result, "usage_metadata", None)
    if isinstance(usage, dict):
        total = usage.get("total_tokens")
        if isinstance(total, int):
            return total
        it = usage.get("input_tokens") or 0
        ot = usage.get("output_tokens") or 0
        if isinstance(it, int) and isinstance(ot, int):
            return it + ot
    meta = getattr(result, "response_metadata", None)
    if isinstance(meta, dict):
        tu = meta.get("token_usage")
        if isinstance(tu, dict):
            total = tu.get("total_tokens")
            if isinstance(total, int):
                return total
    return 0


async def account_llm_result(purpose: str, result: Any) -> None:
    """收口：从结果取 token → 计入指标 + 累计当前租户当日预算（best-effort）。"""
    tokens = extract_token_usage(result)
    if tokens <= 0:
        return
    tenant = get_current_tenant()
    from app.core.metrics import count_llm_tokens

    # 指标只记聚合（Stage 25 基数整改）；租户明细由下方 Redis 日计数承载
    count_llm_tokens(purpose, tokens)
    await record_usage(tenant, tokens)
