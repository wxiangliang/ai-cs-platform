"""知识库定时生效/失效（Stage 16）。

    uv run python scripts/kb_schedule.py [--dry-run]

幂等可重跑，建议 cron 每 5-10 分钟执行：
1. 到点生效：`effective_from <= now` 且尚未发布的文档 → 自动发布（建索引上线），清空 effective_from；
2. 到点失效：`expire_at <= now` 的已生效文档 → archived（退出检索），清空 expire_at。

发布复用 kb_operations_service（重建分块+索引）；单文档失败只告警不中断整批。
"""

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from app.core.logging import get_logger, setup_logging  # noqa: E402
from app.db.session import AsyncSessionLocal, dispose_engine  # noqa: E402
from app.services.kb_operations_service import kb_operations_service  # noqa: E402

logger = get_logger(__name__)


async def publish_effective(dry_run: bool = False) -> int:
    """到点生效：发布 effective_from 已到且尚未上线的文档。"""
    now = datetime.now(timezone.utc)
    sql = text(
        """
        SELECT id, tenant_id FROM kb_document
        WHERE effective_from IS NOT NULL AND effective_from <= :now
          AND status <> 'archived' AND published_version IS NULL
        LIMIT 200
        """
    )
    count = 0
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(sql, {"now": now})).all()
        for doc_id, tenant_id in rows:
            if dry_run:
                logger.info("[dry-run] would publish scheduled doc %s", doc_id)
                continue
            try:
                await kb_operations_service.force_publish(
                    session, tenant_id, doc_id, actor="scheduler"
                )
                await session.execute(
                    text("UPDATE kb_document SET effective_from = NULL WHERE id = :i"),
                    {"i": doc_id},
                )
                count += 1
            except Exception:  # noqa: BLE001 - 单文档失败不中断整批
                logger.exception("scheduled publish failed: doc=%s", doc_id)
        await session.commit()
    logger.info("published %d scheduled documents", count)
    return count


async def archive_expired(dry_run: bool = False) -> int:
    """到点失效：archive expire_at 已到的文档（退出检索）。"""
    now = datetime.now(timezone.utc)
    sql = text(
        """
        SELECT id, tenant_id FROM kb_document
        WHERE expire_at IS NOT NULL AND expire_at <= :now AND status <> 'archived'
        LIMIT 200
        """
    )
    count = 0
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(sql, {"now": now})).all()
        for doc_id, tenant_id in rows:
            if dry_run:
                logger.info("[dry-run] would archive expired doc %s", doc_id)
                continue
            try:
                await kb_operations_service.archive(session, tenant_id, doc_id, editor="scheduler")
                await session.execute(
                    text("UPDATE kb_document SET expire_at = NULL WHERE id = :i"),
                    {"i": doc_id},
                )
                count += 1
            except Exception:  # noqa: BLE001
                logger.exception("scheduled archive failed: doc=%s", doc_id)
        await session.commit()
    logger.info("archived %d expired documents", count)
    return count


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    setup_logging()
    await publish_effective(dry_run=args.dry_run)
    await archive_expired(dry_run=args.dry_run)
    await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
