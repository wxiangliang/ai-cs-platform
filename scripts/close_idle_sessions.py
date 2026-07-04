"""会话生命周期定时任务（Stage 15）：空闲会话关闭 + 超时工单关单。

    uv run python scripts/close_idle_sessions.py [--dry-run]

幂等可重跑，建议 cron 每小时执行：
1. 空闲关闭：active 会话最后一条消息早于 SESSION_IDLE_CLOSE_HOURS → closed，
   状态机复位（任务不复活），可选发送 CSAT 询问（CSAT_ON_CLOSE）；
   closed 会话来新消息由 load_session_state 自动重开；
2. 工单超时：ASSIGNED 超过 TICKET_STALE_HOURS 无坐席动作 → CLOSED + 会话归还
   （补 Stage 07 遗留的 CLOSED 路径；PENDING 不自动关——没人认领是排班问题，
   自动关掉等于丢单）。
"""

import argparse
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.logging import get_logger, setup_logging  # noqa: E402
from app.db.session import AsyncSessionLocal, dispose_engine  # noqa: E402
from app.repositories.chat_dialog_state_repository import (  # noqa: E402
    chat_dialog_state_repository,
)
from app.repositories.chat_message_repository import chat_message_repository  # noqa: E402
from app.repositories.chat_task_repository import chat_task_repository  # noqa: E402

logger = get_logger(__name__)


async def close_idle_sessions(dry_run: bool = False) -> int:
    """关闭空闲会话，返回处理条数。"""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=settings.SESSION_IDLE_CLOSE_HOURS)
    # 最后一条消息早于阈值的 active 会话（无消息的按会话创建时间算）
    sql = text(
        """
        SELECT s.id, s.tenant_id, s.user_id
        FROM chat_session s
        WHERE s.status = 'active'
          AND COALESCE(
                (SELECT max(m.created_at) FROM chat_message m WHERE m.session_id = s.id),
                s.created_at
              ) < :cutoff
        LIMIT 500
        """
    )
    closed = 0
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(sql, {"cutoff": cutoff})).all()
        for sid, tenant_id, _user_id in rows:
            if dry_run:
                logger.info("[dry-run] would close idle session %s", sid)
                continue
            await session.execute(
                text("UPDATE chat_session SET status='closed' WHERE id=:sid"), {"sid": sid}
            )
            # 状态机复位：closed 会话重开时不复活旧任务（与 TASK_TTL 语义一致）
            await chat_dialog_state_repository.upsert_by_session_id(
                session, tenant_id, sid,
                state="IDLE", active_task_json=None, task_stack_json=[],
                context_stacks_json=(
                    {"csat_pending": "session_close"} if settings.CSAT_ON_CLOSE else {}
                ),
            )
            # 终结未完成任务行，避免僵尸行（Stage 15）
            await chat_task_repository.abort_open_by_session(session, tenant_id, sid)
            if settings.CSAT_ON_CLOSE:
                from app.core.i18n import t
                from app.repositories.chat_session_repository import chat_session_repository

                sess = await chat_session_repository.get_by_id(session, sid)
                loc = (sess.metadata_json or {}).get("locale") if sess is not None else None
                await chat_message_repository.create(
                    session, tenant_id=tenant_id, session_id=sid, role="assistant",
                    content=t("csat.ask_session_close", loc),
                    status="DONE", metadata_json={"csat_request": "session_close"},
                )
            closed += 1
        await session.commit()
    logger.info("closed %d idle sessions (cutoff=%s)", closed, cutoff.isoformat())
    return closed


async def close_stale_tickets(dry_run: bool = False) -> int:
    """关闭超时工单并归还会话，返回处理条数。"""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=settings.TICKET_STALE_HOURS)
    sql = text(
        """
        SELECT id, tenant_id, session_id FROM chat_handoff_ticket
        WHERE status = 'ASSIGNED' AND updated_at < :cutoff
        LIMIT 200
        """
    )
    closed = 0
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(sql, {"cutoff": cutoff})).all()
        for ticket_id, tenant_id, sid in rows:
            if dry_run:
                logger.info("[dry-run] would close stale ticket %s", ticket_id)
                continue
            await session.execute(
                text("UPDATE chat_handoff_ticket SET status='CLOSED' WHERE id=:tid"),
                {"tid": ticket_id},
            )
            # 会话归还：bot 恢复应答，状态机复位
            await session.execute(
                text(
                    "UPDATE chat_session SET status='active' "
                    "WHERE id=:sid AND tenant_id=:t AND status='handoff'"
                ),
                {"sid": sid, "t": tenant_id},
            )
            await chat_dialog_state_repository.upsert_by_session_id(
                session, tenant_id, sid,
                state="IDLE", active_task_json=None, task_stack_json=[],
            )
            await chat_task_repository.abort_open_by_session(session, tenant_id, sid)
            closed += 1
        await session.commit()
    logger.info("closed %d stale tickets (cutoff=%s)", closed, cutoff.isoformat())
    return closed


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    setup_logging()
    await close_idle_sessions(dry_run=args.dry_run)
    await close_stale_tickets(dry_run=args.dry_run)
    await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
