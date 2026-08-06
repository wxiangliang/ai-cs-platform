"""客户旅程服务（Stage 38：全规则推导，不用 LLM 猜阶段）。

推进规则（需求第 1 节）：阶段单调不倒退；弱证据一次只推进一格且不能
跳高风险阶段；at_risk 是叠加标记与阶段共存（「已购客户在闹退款」）。
更新收口 save_turn（fail-open），先于 NBA 决策——本轮阶段立即可用于
活动资格（eligible_journey_stages）。
"""

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.state.types import TurnStatus
from app.core.logging import get_logger
from app.core.metrics import count_journey

logger = get_logger(__name__)

STAGE_RANK = {
    "NEW": 0, "REGISTERED": 1, "DISCOVERING": 2,
    "CONSIDERING": 3, "READY_TO_BUY": 4, "PURCHASED": 5,
}

# 强证据（可跳阶）：意图/事件 → 目标阶段
_STRONG_TARGETS = {
    "ORDER.CREATE": "READY_TO_BUY",
    "ORDER.QUERY_STATUS": "PURCHASED",
    "LOGISTICS.": "PURCHASED",
    "AFTERSALE.": "PURCHASED",  # 有订单可售后=已购（风险另记 at_risk）
    "APPOINTMENT.": "PURCHASED",  # 约安装/维修=已购（Stage 39）
}
# 弱证据（一次最多推进一格）
_WEAK_TARGETS = {
    "PRODUCT.ASK_INFO": "DISCOVERING",
    "PRODUCT.RECOMMEND": "DISCOVERING",
    "PRODUCT.ASK_PRICE": "CONSIDERING",
    "PRODUCT.ASK_STOCK": "CONSIDERING",
    "PRODUCT.COMPARE": "CONSIDERING",
}
_RISK_INTENTS = ("AFTERSALE.COMPLAIN", "AFTERSALE.REFUND")


def derive_transition(
    current_stage: str, intent: str, status: str, csat: int | None = None
) -> dict[str, Any] | None:
    """本轮信号 → 转移决定（纯函数，测试锁定）。

    返回 {stage?, at_risk?, signal}；无可用信号返回 None。
    """
    result: dict[str, Any] = {}
    intent = intent or ""

    # at_risk 叠加/解除（与阶段推进独立）
    if intent in _RISK_INTENTS or (csat is not None and csat <= 2):
        result["at_risk"] = True
        result["signal"] = f"risk:{intent or 'low_csat'}"
    elif csat is not None and csat >= 4:
        result["at_risk"] = False
        result["signal"] = "risk_cleared:csat"

    target = None
    strong = False
    if intent == "MEMBER.REGISTER" and status == TurnStatus.CONFIRMED:
        target, strong = "REGISTERED", True
    else:
        for prefix, stage in _STRONG_TARGETS.items():
            if intent == prefix or (prefix.endswith(".") and intent.startswith(prefix)):
                target, strong = stage, True
                break
        if target is None:
            target = _WEAK_TARGETS.get(intent)

    if target is not None:
        cur = STAGE_RANK.get(current_stage, 0)
        goal = STAGE_RANK[target]
        if goal > cur:
            # 弱证据防抖：一次只推进一格（包纪律「弱证据不能单独推动高风险跳转」）
            new_rank = goal if strong else min(goal, cur + 1)
            new_stage = next(s for s, r in STAGE_RANK.items() if r == new_rank)
            result["stage"] = new_stage
            result.setdefault("signal", f"intent:{intent}")

    return result or None


class JourneyService:
    """旅程读写（tenant+user 唯一行，upsert 语义）。"""

    async def get_stage(
        self, session: AsyncSession, tenant_id: str, user_id: str
    ) -> dict[str, Any] | None:
        from app.repositories.customer_journey_repository import customer_journey_repository

        row = await customer_journey_repository.get_by_user(session, tenant_id, user_id)
        if row is None:
            return None
        return {"stage": row.stage, "at_risk": row.at_risk}

    async def update_from_turn(
        self,
        session: AsyncSession,
        *,
        tenant_id: str,
        user_id: str,
        session_id: str,
        intent: str,
        status: str,
        csat: int | None = None,
    ) -> dict[str, Any] | None:
        """按本轮信号推进旅程；无信号/无变化不写。返回应用后的转移。"""
        from app.repositories.customer_journey_repository import customer_journey_repository

        row = await customer_journey_repository.get_by_user(session, tenant_id, user_id)
        current = row.stage if row else "NEW"
        transition = derive_transition(current, intent, status, csat)
        if transition is None:
            return None
        new_stage = transition.get("stage")
        new_risk = transition.get("at_risk")
        # 无实际变化不写（同会话同阶段目标天然不重复）
        if row is not None and (new_stage is None or new_stage == row.stage) and (
            new_risk is None or new_risk == row.at_risk
        ):
            return None

        entry = {
            "signal": transition.get("signal", ""),
            "from": current,
            "to": new_stage or current,
            "session_id": session_id,
            "at": datetime.now(timezone.utc).isoformat(),
        }
        if row is None:
            await customer_journey_repository.create(
                session,
                tenant_id=tenant_id,
                user_id=user_id,
                stage=new_stage or "NEW",
                at_risk=bool(new_risk) if new_risk is not None else False,
                signals_json=[entry],
            )
        else:
            if new_stage:
                row.stage = new_stage
            if new_risk is not None:
                row.at_risk = new_risk
            history = list(row.signals_json or [])
            history.append(entry)
            row.signals_json = history[-20:]  # 近 20 条封顶
        if new_stage:
            count_journey(new_stage)
        return transition


# 模块级单例
journey_service = JourneyService()
