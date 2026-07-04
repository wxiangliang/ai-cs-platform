"""节点：guardrail_check —— 输入护栏（Stage 14 实装）。

三层输入检查（规则层零延迟，规则库见 guardrails.md 机器可读附表）：
1. 注入模式（「忽略以上指令」等）→ 拦截，固定安全话术；
2. 重度违禁词（辱骂/违法）→ 拦截 + Redis 连击计数，连续 N 次建
   转人工工单（reason=ABUSE）并静默会话；
3. 轻度情绪词 → 不拦截，打 emotion=negative 标记（供回复软化话术）；
另：同文本重复灌注达上限 → 拦截提示换说法（防刷 LLM 成本）。

红线：护栏拦截是「本轮失败」（TurnStatus.FAILED），不触发状态机流转——
不能因为一条被拦截的消息把进行中的 COLLECTING/CONFIRMING 状态打成 FAILED
（v2 语义维持，连击/灌注计数走 Redis 而非 dialog_state 正因如此）；
护栏引擎/Redis 故障一律放行（fail-open），绝不打断主链路。
拦截话术走 i18n（Stage 19，key: guardrail.<category>）。
"""

from typing import Any

from langchain_core.runnables import RunnableConfig

from app.chat.graph.state import GraphState, get_db_session_from_config
from app.chat.guardrail.engine import guardrail_engine
from app.chat.state.types import TurnStatus
from app.core.config import settings
from app.core.i18n import t
from app.core.logging import get_logger
from app.core.metrics import count_guardrail_block

logger = get_logger(__name__)


async def guardrail_check(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    """输入护栏检查。"""
    # 上游短路轮次透传（Stage 07 静默 / Stage 15 CSAT 捕获等）：
    # blocked 已在 load_session_state 置位，不能被这里的「通过→blocked=False」覆盖
    if state.get("handoff_silent") or state.get("blocked"):
        return {"graph_trace": ["guardrail_check"]}

    tenant_id = state["tenant_id"]
    session_id = state["session_id"]
    normalized = state.get("normalized_text", "")
    locale = state.get("locale")

    # 空消息：拦截并标记本轮失败（正常情况下已被 Schema 的 min_length 拦在 422）
    if not normalized:
        return {
            "blocked": True,
            "guardrail": {"passed": False, "reason": "EMPTY_MESSAGE"},
            "status": TurnStatus.FAILED,
            "graph_trace": ["guardrail_check"],
        }

    # —— 规则层：注入 / 重度违禁 / 情绪标记（Stage 14）——
    verdict = guardrail_engine.check_input(normalized)
    if verdict.action == "block":
        count_guardrail_block(verdict.rule_id)
        guard: dict[str, Any] = {
            "passed": False,
            "rule_id": verdict.rule_id,
            "category": verdict.category,
            "action": "block",
        }
        result: dict[str, Any] = {
            "blocked": True,
            "guardrail": guard,
            "status": TurnStatus.FAILED,
            "guardrail_reply": (
                t(f"guardrail.{verdict.category}", locale) if verdict.category else ""
            ),
            "graph_trace": ["guardrail_check"],
        }
        # 重度违禁连击：连续 N 次 → 建单（reason=ABUSE）+ 会话静默
        if verdict.category == "abuse_severe":
            streak = await guardrail_engine.bump_abuse_streak(tenant_id, session_id)
            guard["abuse_streak"] = streak
            if streak >= settings.GUARDRAIL_ABUSE_STREAK:
                try:
                    from app.repositories.chat_session_repository import chat_session_repository
                    from app.services.handoff_service import REASON_ABUSE, handoff_service

                    session = get_db_session_from_config(config)
                    ticket_id, created = await handoff_service.ensure_ticket(
                        session,
                        tenant_id=tenant_id,
                        session_id=session_id,
                        user_id=state.get("user_id", ""),
                        reason=REASON_ABUSE,
                    )
                    await chat_session_repository.update_status(
                        session, tenant_id, session_id, "handoff"
                    )
                    guard["handoff"] = {
                        "reason": REASON_ABUSE, "ticket_id": ticket_id, "created": created,
                    }
                    result["guardrail_reply"] = t("guardrail.abuse_handoff", locale)
                except Exception:  # noqa: BLE001 - 建单失败只告警，拦截照常生效
                    logger.exception("abuse handoff ticket failed")
        logger.warning(
            "guardrail blocked: rule=%s category=%s session=%s",
            verdict.rule_id, verdict.category, session_id,
        )
        return result

    # —— 通过：重置连击；重复灌注检查 ——
    await guardrail_engine.reset_abuse_streak(tenant_id, session_id)
    if await guardrail_engine.check_repeat_flood(tenant_id, session_id, normalized):
        count_guardrail_block("REPEAT_FLOOD")
        return {
            "blocked": True,
            "guardrail": {
                "passed": False, "rule_id": "REPEAT_FLOOD",
                "category": "repeat_flood", "action": "block",
            },
            "status": TurnStatus.FAILED,
            "guardrail_reply": t("guardrail.repeat_flood", locale),
            "graph_trace": ["guardrail_check"],
        }

    passed: dict[str, Any] = {"passed": True}
    result = {"blocked": False, "guardrail": passed, "graph_trace": ["guardrail_check"]}
    if verdict.flags:
        # 轻度情绪：不拦截，标记供回复润色软化话术（决策日志可见）
        passed["flags"] = verdict.flags
        passed["action"] = "flag"
        result["emotion"] = "negative"
    return result
