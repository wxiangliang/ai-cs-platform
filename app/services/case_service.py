"""Case 服务（Stage 34：生命周期 / 幂等合并 / SLA / 补偿评估）。

Case ≠ Task：跨会话的客户问题服务记录。设计要点：
- **一个收口点自动创建**：handoff_service.ensure_ticket 建单成功后调
  open_or_merge（五类触发零改动）+ save_turn 的 CSAT 低分轮；
- **幂等合并**：同客户同类型只有一个活跃 Case，重复触发并入
  （追加关联，新触发更高优先级则提级并重算 SLA）；
- **状态机白名单**：非法迁移拒绝（API 层 409）；
- **补偿 = 纯政策表判定**（Service Recovery 并入）：LLM 不参与；
  v1 只判资格不发放（发放是写操作，随真实优惠券系统走确认门，遗留 1）。
"""

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.core.metrics import count_case
from app.models.chat_service_case import ACTIVE_CASE_STATUSES, ChatServiceCase
from app.repositories.chat_service_case_repository import chat_service_case_repository

logger = get_logger(__name__)

# 触发原因（handoff reason / csat）→ (case_type, priority)
REASON_CASE_MAP: dict[str, tuple[str, str]] = {
    "USER_REQUEST": ("GENERAL_SUPPORT", "NORMAL"),
    "MANUAL": ("GENERAL_SUPPORT", "NORMAL"),
    "PAYMENT_ISSUE": ("PAYMENT_ISSUE", "HIGH"),
    "EXECUTION_FAILED": ("EXECUTION_FAILURE", "HIGH"),
    "REPEATED_UNKNOWN": ("UNRESOLVED_QUERY", "NORMAL"),
    "SKILL_RULE": ("POLICY_REVIEW", "NORMAL"),
    "ABUSE": ("ABUSE", "NORMAL"),
    "LOW_CSAT": ("SERVICE_QUALITY", "NORMAL"),
}

_PRIORITY_RANK = {"LOW": 0, "NORMAL": 1, "HIGH": 2}

# 状态机白名单：{当前状态: 允许迁移到}
STATUS_TRANSITIONS: dict[str, set[str]] = {
    "OPEN": {"IN_PROGRESS", "WAITING_CUSTOMER", "WAITING_INTERNAL",
             "WAITING_EXTERNAL", "RESOLVED", "CLOSED", "ESCALATED"},
    "IN_PROGRESS": {"WAITING_CUSTOMER", "WAITING_INTERNAL", "WAITING_EXTERNAL",
                    "RESOLVED", "CLOSED", "ESCALATED"},
    "WAITING_CUSTOMER": {"IN_PROGRESS", "RESOLVED", "CLOSED", "ESCALATED"},
    "WAITING_INTERNAL": {"IN_PROGRESS", "RESOLVED", "CLOSED", "ESCALATED"},
    "WAITING_EXTERNAL": {"IN_PROGRESS", "RESOLVED", "CLOSED", "ESCALATED"},
    "ESCALATED": {"IN_PROGRESS", "WAITING_CUSTOMER", "WAITING_INTERNAL",
                  "WAITING_EXTERNAL", "RESOLVED", "CLOSED"},
    "RESOLVED": {"CLOSED", "REOPENED"},
    "CLOSED": {"REOPENED"},
    "REOPENED": {"IN_PROGRESS", "WAITING_CUSTOMER", "WAITING_INTERNAL",
                 "WAITING_EXTERNAL", "RESOLVED", "CLOSED", "ESCALATED"},
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def sla_due(priority: str, now: datetime | None = None) -> datetime:
    """按优先级算 SLA 解决时限。"""
    hours = {
        "HIGH": settings.CASE_SLA_HOURS_HIGH,
        "NORMAL": settings.CASE_SLA_HOURS_NORMAL,
        "LOW": settings.CASE_SLA_HOURS_LOW,
    }.get(priority, settings.CASE_SLA_HOURS_NORMAL)
    return (now or _now()) + timedelta(hours=hours)


def _merge_related(case: ChatServiceCase, refs: dict[str, Any]) -> dict[str, Any]:
    """关联合并（去重保序）。"""
    related = dict(case.related_json or {})
    for key, values in refs.items():
        if not values:
            continue
        seen = list(related.get(key) or [])
        for v in values:
            if v and v not in seen:
                seen.append(v)
        related[key] = seen
    return related


class CaseService:
    """服务 Case 生命周期管理。"""

    async def open_or_merge(
        self,
        session: AsyncSession,
        *,
        tenant_id: str,
        user_id: str,
        reason: str,
        refs: dict[str, Any] | None = None,
    ) -> tuple[str, bool]:
        """按触发原因开/并 Case（幂等）：返回 (case_id, 是否新建)。

        同客户同类型有活跃 Case → 并入：追加关联；新触发优先级更高 →
        提级并重算 SLA。竞态撞部分唯一索引由调用方 SAVEPOINT 包裹兜底。
        """
        case_type, priority = REASON_CASE_MAP.get(reason, ("GENERAL_SUPPORT", "NORMAL"))
        refs = refs or {}
        existing = await chat_service_case_repository.get_active_by_user_type(
            session, tenant_id, user_id, case_type
        )
        if existing is not None:
            updates: dict[str, Any] = {"related_json": _merge_related(existing, refs)}
            if _PRIORITY_RANK.get(priority, 1) > _PRIORITY_RANK.get(existing.priority, 1):
                updates["priority"] = priority
                updates["sla_due_at"] = sla_due(priority)
            for k, v in updates.items():
                setattr(existing, k, v)
            count_case("merged")
            return existing.id, False

        now = _now()
        case = await chat_service_case_repository.create(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            case_no=f"CS{now:%Y%m%d}{uuid.uuid4().hex[:8].upper()}",
            case_type=case_type,
            status="OPEN",
            priority=priority,
            owner_type="AI",
            sla_due_at=sla_due(priority, now),
            related_json={k: [v for v in vs if v] for k, vs in refs.items() if vs},
            metadata_json={"opened_by": reason},
        )
        count_case("opened")
        return case.id, True

    def can_transition(self, current: str, target: str) -> bool:
        return target in STATUS_TRANSITIONS.get(current, set())

    async def transition(
        self,
        session: AsyncSession,
        case: ChatServiceCase,
        target: str,
        *,
        resolution_code: str | None = None,
        operator: str | None = None,
    ) -> bool:
        """状态流转（白名单校验）；RESOLVED/CLOSED/REOPENED 附带副作用。"""
        if not self.can_transition(case.status, target):
            return False
        now = _now()
        case.status = target
        if operator:
            case.owner_type = "HUMAN"
            case.owner_id = operator
        if target == "RESOLVED":
            case.resolution_code = resolution_code or case.resolution_code
            case.resolved_at = now
            count_case("resolved")
        elif target == "CLOSED":
            case.closed_at = now
            count_case("closed")
        elif target == "REOPENED":
            case.reopen_count = (case.reopen_count or 0) + 1
            case.resolution_code = None
            case.resolved_at = None
            case.closed_at = None
            case.sla_due_at = sla_due(case.priority, now)
            count_case("reopened")
        return True

    async def escalate_breached(self, session: AsyncSession, now: datetime | None = None) -> int:
        """SLA 超时升级（cron）：活跃且过期 → ESCALATED + HIGH，幂等。"""
        now = now or _now()
        breached = await chat_service_case_repository.list_sla_breached(session, now)
        for case in breached:
            case.status = "ESCALATED"
            case.priority = "HIGH"
            meta = dict(case.metadata_json or {})
            events = list(meta.get("escalations") or [])
            events.append({"at": now.isoformat(), "reason": "SLA_BREACH"})
            meta["escalations"] = events
            case.metadata_json = meta
            count_case("escalated")
        if breached:
            # 显式 flush：同事务后续查询立即可见（幂等口径），提交仍由调用方
            await session.flush()
        return len(breached)


# ---------------------------------------------------------------------------
# Service Recovery：补偿政策评估（纯政策表，LLM 不参与）
# ---------------------------------------------------------------------------

_policy_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}


def load_compensation_policies() -> list[dict[str, Any]]:
    """加载补偿政策（mtime 缓存，缺失/损坏返回 []——无政策=一律不符合）。"""
    path = settings.COMPENSATION_POLICY_PATH
    if not path:
        return []
    p = Path(path)
    if not p.is_absolute():
        p = Path(__file__).resolve().parents[2] / p
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return []
    cached = _policy_cache.get(path)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        policies = [x for x in data if isinstance(x, dict) and x.get("policy_code")]
    except Exception:  # noqa: BLE001
        logger.warning("compensation policy config invalid: %s", p, exc_info=True)
        policies = []
    _policy_cache[path] = (mtime, policies)
    return policies


def evaluate_compensation(case: ChatServiceCase) -> dict[str, Any]:
    """补偿资格判定：命中首条匹配政策；reason_codes 全程留痕（审计口径）。

    红线：纯政策表判定，LLM 不参与；只判资格不发放（发放走确认门，遗留 1）。
    """
    reason_codes: list[str] = [f"case_type:{case.case_type}", f"priority:{case.priority}"]
    if case.status not in ACTIVE_CASE_STATUSES:
        return {"eligible": False, "reason_codes": [*reason_codes, "case_not_active"]}
    already = (case.metadata_json or {}).get("compensation")
    if already:
        return {
            "eligible": False,
            "reason_codes": [*reason_codes, "already_evaluated"],
            "previous": already,
        }
    for policy in load_compensation_policies():
        if case.case_type not in (policy.get("case_types") or []):
            continue
        min_p = _PRIORITY_RANK.get(str(policy.get("min_priority") or "LOW"), 0)
        if _PRIORITY_RANK.get(case.priority, 1) < min_p:
            reason_codes.append(f"below_min_priority:{policy['policy_code']}")
            continue
        return {
            "eligible": True,
            "policy_code": policy["policy_code"],
            "compensation_type": policy.get("compensation_type", "COUPON"),
            "max_value": policy.get("max_value", 0),
            "requires_approval": bool(policy.get("requires_approval", True)),
            "reason_codes": [*reason_codes, f"policy:{policy['policy_code']}"],
        }
    return {"eligible": False, "reason_codes": [*reason_codes, "no_matching_policy"]}


# 模块级单例
case_service = CaseService()
