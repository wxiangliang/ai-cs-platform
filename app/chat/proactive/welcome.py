"""开场引导（Session Welcome，Stage 41 需求第 3.1 节）。

会话创建成功后（用户还没说话）发一条轻量欢迎/引导消息——补齐「响应式客服
开场完全沉默」的缺口。红线：

1. **轻量单条**：欢迎+能力提示（可按旅程阶段选变体），不是流程——用户接下来
   说什么走完全正常的主链路；不做任何前端行为检测；
2. **确定性文案**：hook_key 指向 i18n 模板（运营配置），不经 LLM；
3. **fail-closed**（营销方向纪律）：无 user_id / opt-out / at_risk / 频控命中 /
   Redis 故障，一律不发；发送失败绝不打断会话创建（fail-open 包裹）。

频控：`proactive:welcome:{tenant}:{user}` SET NX（TTL 24h）——同客户每日
至多一次开场引导，跨会话生效。closed 会话重开不经会话创建接口，天然不触发。
"""

import json
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.i18n import t
from app.core.logging import get_logger

logger = get_logger(__name__)

# 与 nba.py 同族的 Redis 键（客户级 24h 频控）
_K_WELCOME = "proactive:welcome:{tenant}:{user}"
_K_OPTOUT = "proactive:optout:{tenant}:{user}"

# 配置文件缺失时的内置默认条目（保证开箱体验；配置存在但损坏 = 运营意图
# 不明，宁可不发——两种缺省方向不同，见 load_welcome_configs）
_DEFAULT_CONFIGS = [{"welcome_id": "default", "enabled": True, "hook_key": "proactive.welcome"}]

# mtime 缓存：{path: (mtime, configs)}
_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def load_welcome_configs() -> list[dict[str, Any]]:
    """加载开场引导配置；未配置/文件缺失回退内置默认，损坏返回空表。"""
    path = settings.WELCOME_CONFIG_PATH
    if not path:
        return _DEFAULT_CONFIGS
    p = Path(path)
    if not p.is_absolute():
        p = _repo_root() / p
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return _DEFAULT_CONFIGS
    cached = _cache.get(path)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        configs = [c for c in data if isinstance(c, dict) and c.get("welcome_id") and c.get("hook_key")]
    except Exception:  # noqa: BLE001
        logger.warning("welcome config invalid: %s", p, exc_info=True)
        configs = []
    _cache[path] = (mtime, configs)
    return configs


def select_welcome(journey_stage: str | None) -> dict[str, Any] | None:
    """按旅程阶段选第一条符合的配置（campaigns 门控语义：声明了阶段而
    客户阶段未知/不符 → 跳过；未声明则不限阶段）。"""
    for entry in load_welcome_configs():
        if not entry.get("enabled"):
            continue
        stages = entry.get("eligible_journey_stages") or []
        if stages and journey_stage not in stages:
            continue
        return entry
    return None


async def send_welcome_if_eligible(
    db: AsyncSession, tenant_id: str, session_id: str, user_id: str
) -> dict[str, Any] | None:
    """主入口（chat_service.create_session 调用）：决策+落库+WS 推送。

    返回决策摘要（观测用）或 None（未启用/未发）。任何异常吞掉不打断
    会话创建（外层 fail-open）。
    """
    if not (settings.PROACTIVE_ENABLED and settings.PROACTIVE_WELCOME_ENABLED):
        return None
    try:
        return await _send(db, tenant_id, session_id, user_id)
    except Exception:  # noqa: BLE001 - 开场引导失败绝不打断会话创建
        logger.warning("welcome send failed, skipped", exc_info=True)
        return None


async def _send(
    db: AsyncSession, tenant_id: str, session_id: str, user_id: str
) -> dict[str, Any] | None:
    from app.core.metrics import count_proactive

    # 无稳定 user_id 无法频控 → fail-closed 不发
    if not user_id:
        count_proactive("WELCOME", "suppressed")
        return None

    # —— opt-out（「以后都别推」）与 24h 频控（SET NX 原子占位，Redis 故障不发）——
    try:
        from app.cache.redis_client import get_redis_client

        redis = get_redis_client()
        if await redis.exists(_K_OPTOUT.format(tenant=tenant_id, user=user_id)):
            count_proactive("WELCOME", "suppressed")
            return None
        acquired = await redis.set(
            _K_WELCOME.format(tenant=tenant_id, user=user_id), "1", ex=86400, nx=True
        )
        if not acquired:
            count_proactive("WELCOME", "suppressed")
            return None
    except Exception:  # noqa: BLE001 - fail-closed：频控不可用就不发
        count_proactive("WELCOME", "suppressed")
        return None

    # —— 旅程阶段：门控 + 决策证据（查询失败按未知处理，只影响限定阶段的条目）——
    journey_stage: str | None = None
    at_risk = False
    try:
        from app.services.journey_service import journey_service

        journey = await journey_service.get_stage(db, tenant_id, user_id=user_id)
        if journey:
            journey_stage = journey.get("stage")
            at_risk = bool(journey.get("at_risk"))
    except Exception:  # noqa: BLE001
        journey_stage = None
    if at_risk:
        # 服务修复期客户（低分/投诉/退款中）不做任何开场推介
        count_proactive("WELCOME", "suppressed")
        return None

    entry = select_welcome(journey_stage)
    if entry is None:
        count_proactive("WELCOME", "suppressed")
        return None

    # —— 落库（metadata=发送依据可追溯，Stage 36 先例）+ WS 推送 ——
    content = t(str(entry["hook_key"]), None)
    from app.repositories.chat_message_repository import chat_message_repository
    from app.services.notify_service import session_channel, ws_hub

    message = await chat_message_repository.create(
        db,
        tenant_id=tenant_id,
        session_id=session_id,
        role="assistant",
        content=content,
        status="DONE",
        metadata_json={
            "proactive": True, "category": "welcome",
            "config_id": entry["welcome_id"], "hook_key": entry["hook_key"],
            **({"journey_stage": journey_stage} if journey_stage else {}),
        },
    )
    ws_hub.publish_after_commit(
        db,
        session_channel(tenant_id, session_id),
        {"type": "proactive", "message_id": message.id,
         "category": "welcome", "content": content},
    )
    count_proactive("WELCOME", "applied")
    return {"welcome_id": entry["welcome_id"], "message_id": message.id}
