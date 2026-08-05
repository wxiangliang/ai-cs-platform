"""Next Best Action 规则策略（Stage 31，需求第 3/4/6 节）。

主任务处理完成后决定本轮是否追加**至多一个**主动动作（v1 只有
MENTION_CAMPAIGN）。三条纪律：

1. **抑制优先**：退款/投诉/取消/支付、确认门、负面情绪、人工接管、闲聊轮、
   CSAT 轮一律 NO_ACTION——低优先级内容不得挤占高优先级诉求；
2. **系统动作不是意图**：决策产物只落 decision_log 与回复追加，
   不进意图 taxonomy / SetFit / Meta（三轴独立）；
3. **fail-closed**：Redis 故障按「已达频控」处理（营销宁可少发不能超发，
   与限流 fail-open 方向相反——方向由业务代价决定）。

拒绝检测（v1 规则）：上轮展示标记（10 分钟窗口）+ 本轮拒绝语 → 冷却 48h；
含「以后/都别/永远」类强拒绝 → 永久 opt-out。
"""

import re
from collections.abc import Mapping
from typing import Any

from app.chat.intent.types import DecisionSource
from app.chat.proactive.campaigns import select_campaign
from app.chat.state.types import TurnStatus
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

ACTION_CAMPAIGN = "MENTION_CAMPAIGN"
ACTION_ONBOARDING = "START_ONBOARDING"  # Stage 33：建议未注册用户开通会员
ACTION_NONE = "NONE"

# onboarding 频控复用 impression 键的伪活动名（客户级建议上限）
_ONBOARDING_KEY = "onboarding"

# 高风险意图域/意图：营销绝对禁区（需求第 4 节抑制矩阵）
_HIGH_RISK_DOMAINS = ("AFTERSALE.", "PAYMENT.")
_HIGH_RISK_INTENTS = frozenset({"ORDER.CANCEL"})

_REJECT_RE = re.compile(r"不用推|别推|不需要推荐|不感兴趣|不想看活动|别发这些")
_OPTOUT_RE = re.compile(r"以后|都别|永远|再也")

# Redis 键模板（TTL 见需求第 6 节）
_K_SESSION = "proactive:sess:{tenant}:{session}"
_K_IMPRESSION = "proactive:imp:{tenant}:{user}:{campaign}"
_K_LAST = "proactive:last:{tenant}:{session}"
_K_COOLDOWN = "proactive:cool:{tenant}:{user}"
_K_OPTOUT = "proactive:optout:{tenant}:{user}"


def suppression_reason(state: Mapping[str, Any]) -> str | None:
    """纯函数抑制检查（不含 Redis 频控）：返回首个命中的 reason code。"""
    if state.get("blocked"):
        return "blocked"
    if state.get("handoff_silent") or state.get("status") == TurnStatus.HANDOFF:
        return "handoff"
    if state.get("csat_capture") or (state.get("context_stacks") or {}).get("csat_pending"):
        return "csat"
    if state.get("status") != TurnStatus.DONE:
        # NEEDS_SLOT/NEEDS_CONFIRM/EXECUTING/FALLBACK/FAILED 全含：
        # 补槽与确认门轮天然抑制（主诉求未闭环不叠加营销）
        return "not_done"
    if state.get("emotion") == "negative":
        return "negative_emotion"
    intent_dict = state.get("intent_result") or {}
    intent = str(intent_dict.get("final_intent") or intent_dict.get("pred_label") or "")
    if intent in _HIGH_RISK_INTENTS or intent.startswith(_HIGH_RISK_DOMAINS):
        return "high_risk_flow"
    if intent_dict.get("decision_source") == DecisionSource.MODE_SOCIAL:
        return "social_only"
    mode = (intent_dict.get("mode_gate") or {}).get("mode")
    if mode == "SOCIAL_ONLY":
        return "social_only"
    return None


def _final_intent(state: Mapping[str, Any]) -> str:
    intent_dict = state.get("intent_result") or {}
    return str(intent_dict.get("final_intent") or intent_dict.get("pred_label") or "")


async def decide_proactive(state: Mapping[str, Any]) -> dict[str, Any] | None:
    """主入口（save_turn 回复定稿前调用）：产出决策证据供落库与回复追加。

    返回 None 表示功能未启用；否则恒返回带 reason_codes 的决策对象
    （影子期 applied=False 只观察）。任何异常 fail-closed 为 NO_ACTION。
    """
    if not settings.PROACTIVE_ENABLED:
        return None
    try:
        return await _decide(state)
    except Exception:  # noqa: BLE001 - 主动服务失败绝不打断保存链路
        logger.warning("proactive decide failed, suppressed", exc_info=True)
        return {"action": ACTION_NONE, "suppressed_by": "error", "reason_codes": ["error"]}


async def _decide(state: Mapping[str, Any]) -> dict[str, Any]:
    tenant = state.get("tenant_id", "")
    session_id = state.get("session_id", "")
    user = state.get("user_id", "") or session_id

    # —— 拒绝检测先于一切（上轮展示 + 本轮拒绝语 → 记冷却，本轮必不展示）——
    text = state.get("normalized_text") or ""
    rejected = await _check_rejection(tenant, session_id, user, text)
    if rejected:
        return {"action": ACTION_NONE, "suppressed_by": rejected, "reason_codes": [rejected]}

    reason = suppression_reason(state)
    if reason:
        return {"action": ACTION_NONE, "suppressed_by": reason, "reason_codes": [reason]}

    # —— 冷却/退出/会话频控（Redis，故障 fail-closed）——
    gate = await _redis_gate(tenant, session_id, user)
    if gate:
        return {"action": ACTION_NONE, "suppressed_by": gate, "reason_codes": [gate]}

    intent = _final_intent(state)

    # —— Stage 33 会员注册建议（获客优先于营销，包 stage-33 低风险动作第一批）——
    # 条件：有稳定 user_id + 会员状态查询确认未注册（失败/不可用一律跳过
    # 不骚扰=fail-closed）+ 客户级建议上限未满。话术引导显式回复「注册」
    # （确定性入口走规则层触发，不做弱确认解析）
    if settings.PROACTIVE_ONBOARDING_ENABLED and state.get("user_id"):
        user_id = str(state["user_id"])
        capped = (
            await _impressions(tenant, user, _ONBOARDING_KEY)
            >= settings.PROACTIVE_ONBOARDING_MAX
        )
        if not capped and await _member_unregistered(tenant, user_id):
            applied = bool(settings.PROACTIVE_APPLY)
            if applied:
                await _record_impression(tenant, session_id, user, _ONBOARDING_KEY)
            return {
                "action": ACTION_ONBOARDING,
                "applied": applied,
                "reason_codes": ["unregistered_user", f"intent:{intent}"],
            }

    campaign = select_campaign(intent)
    if campaign is None:
        return {"action": ACTION_NONE, "suppressed_by": "no_campaign", "reason_codes": ["no_campaign"]}

    # —— 客户×活动频控 ——
    cap = int(campaign.get("max_per_customer") or 0)
    if cap and await _impressions(tenant, user, campaign["campaign_id"]) >= cap:
        return {"action": ACTION_NONE, "suppressed_by": "campaign_cap", "reason_codes": ["campaign_cap"]}

    applied = bool(settings.PROACTIVE_APPLY)
    decision = {
        "action": ACTION_CAMPAIGN,
        "campaign_id": campaign["campaign_id"],
        # 展示可追溯到活动版本（包 stage-34 验收）：运营改配置需递增 version
        "campaign_version": campaign.get("version"),
        "hook": str(campaign.get("hook") or ""),
        "applied": applied,
        "reason_codes": ["eligible", f"intent:{intent}"],
    }
    if applied:
        await _record_impression(tenant, session_id, user, campaign["campaign_id"])
    return decision


# ---------------------------------------------------------------------------
# Redis 频控/冷却（全部 fail-closed：异常按「不允许展示」处理）
# ---------------------------------------------------------------------------


async def _member_unregistered(tenant: str, user_id: str) -> bool:
    """经工具层查会员状态（provider 抽象：mock/MCP/真实系统同口径）。

    只有明确返回「未注册」才建议；查询失败/服务不可达/字段缺失一律 False
    （fail-closed：不确定就不骚扰）。
    """
    try:
        from app.chat.tools.factory import get_tool_provider

        result = await get_tool_provider().invoke(
            "query_member_status", {"user_id": user_id}, tenant_id=tenant
        )
        return bool(result.ok and result.data.get("registered") is False)
    except Exception:  # noqa: BLE001
        return False


async def _check_rejection(tenant: str, session_id: str, user: str, text: str) -> str | None:
    """上轮展示过且本轮是拒绝语 → 记冷却（强拒绝 opt-out），返回抑制码。"""
    if not text or not _REJECT_RE.search(text):
        return None
    try:
        from app.cache.redis_client import get_redis_client

        redis = get_redis_client()
        if not await redis.exists(_K_LAST.format(tenant=tenant, session=session_id)):
            return None  # 没展示过，「别推」类表达不算对我们的拒绝
        if _OPTOUT_RE.search(text):
            await redis.set(_K_OPTOUT.format(tenant=tenant, user=user), "1")
            return "opted_out"
        await redis.set(
            _K_COOLDOWN.format(tenant=tenant, user=user),
            "1",
            ex=settings.PROACTIVE_REJECT_COOLDOWN_HOURS * 3600,
        )
        return "rejected_cooldown"
    except Exception:  # noqa: BLE001
        return "redis_unavailable"


async def _redis_gate(tenant: str, session_id: str, user: str) -> str | None:
    try:
        from app.cache.redis_client import get_redis_client

        redis = get_redis_client()
        if await redis.exists(_K_OPTOUT.format(tenant=tenant, user=user)):
            return "opted_out"
        if await redis.exists(_K_COOLDOWN.format(tenant=tenant, user=user)):
            return "rejected_cooldown"
        shown = await redis.get(_K_SESSION.format(tenant=tenant, session=session_id))
        if shown is not None and int(shown) >= settings.PROACTIVE_SESSION_MAX:
            return "session_cap"
        return None
    except Exception:  # noqa: BLE001 - fail-closed：查不到频控就当已达上限
        return "redis_unavailable"


async def _impressions(tenant: str, user: str, campaign_id: str) -> int:
    try:
        from app.cache.redis_client import get_redis_client

        value = await get_redis_client().get(
            _K_IMPRESSION.format(tenant=tenant, user=user, campaign=campaign_id)
        )
        return int(value) if value is not None else 0
    except Exception:  # noqa: BLE001 - fail-closed
        return 10**9


async def _record_impression(tenant: str, session_id: str, user: str, campaign_id: str) -> None:
    """真实展示后记账：会话计数（1 天）/客户×活动计数（30 天）/上轮标记（10 分钟）。"""
    try:
        from app.cache.redis_client import get_redis_client

        redis = get_redis_client()
        pipe = redis.pipeline()
        key_sess = _K_SESSION.format(tenant=tenant, session=session_id)
        key_imp = _K_IMPRESSION.format(tenant=tenant, user=user, campaign=campaign_id)
        pipe.incr(key_sess)
        pipe.expire(key_sess, 86400)
        pipe.incr(key_imp)
        pipe.expire(key_imp, 30 * 86400)
        pipe.set(_K_LAST.format(tenant=tenant, session=session_id), campaign_id, ex=600)
        await pipe.execute()
    except Exception:  # noqa: BLE001 - 记账失败只影响频控精度，不打断回复
        logger.warning("proactive impression record failed", exc_info=True)
