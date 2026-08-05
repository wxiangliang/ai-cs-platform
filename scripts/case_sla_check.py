"""Case SLA 巡检 cron（Stage 34，deploy/scheduler 定时执行）。

    uv run python scripts/case_sla_check.py

两件事：
1. 超时升级：活跃 Case sla_due_at 过期 → ESCALATED + HIGH（幂等，
   已 ESCALATED 不重复）；
2. 即将超时提醒：剩余 < 25% → 指标计数（坐席实时推送记遗留）。
"""

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


async def main() -> None:
    from sqlalchemy import select

    from app.core.metrics import count_case
    from app.db.session import AsyncSessionLocal, dispose_engine
    from app.models.chat_service_case import ACTIVE_CASE_STATUSES, ChatServiceCase
    from app.services.case_service import case_service, sla_due  # noqa: F401

    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as session:
        escalated = await case_service.escalate_breached(session, now)

        # 即将超时（剩余 < 25% 总时限）：只计指标
        stmt = select(ChatServiceCase).where(
            ChatServiceCase.status.in_(
                tuple(s for s in ACTIVE_CASE_STATUSES if s != "ESCALATED")
            ),
            ChatServiceCase.sla_due_at.is_not(None),
            ChatServiceCase.sla_due_at >= now,
        )
        warning = 0
        for case in (await session.execute(stmt)).scalars():
            total = (case.sla_due_at - case.created_at).total_seconds()
            remain = (case.sla_due_at - now).total_seconds()
            if total > 0 and remain / total < 0.25:
                count_case("sla_warning")
                warning += 1
        await session.commit()
    await dispose_engine()
    print(f"case sla check: escalated={escalated} warning={warning}")


if __name__ == "__main__":
    asyncio.run(main())
