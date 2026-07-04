"""知识库运营编排服务（Stage 16）。

把知识库文档从「能写能查」升级为「可运营治理」：草稿-审核-发布状态机、
版本历史与回滚、定时生效/失效。

状态机：draft（草稿，不进检索）→ pending_review（待审）→ published（发布，建索引）
→ archived（下线，可回滚）。生效判据看 `published_version` 是否非空且未 archived——
所以编辑已发布文档（status 回到 draft）时，`published_version` 不变，**线上仍服务旧版本**，
直到再次 approve/publish 才切换。审计（谁/何时/动作/意见）落 metadata_json.review_log。
"""

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.kb.ingest import kb_ingest_service
from app.models.kb_document import KbDocument
from app.repositories.kb_document_repository import kb_document_repository
from app.repositories.kb_document_version_repository import kb_document_version_repository

logger = get_logger(__name__)


class KbOperationsService:
    """文档版本/审核/发布/回滚编排。"""

    async def create_draft(
        self,
        session: AsyncSession,
        tenant_id: str,
        *,
        title: str,
        raw_content: str,
        source_type: str = "policy",
        editor: str | None = None,
        note: str | None = None,
        effective_from: datetime | None = None,
        expire_at: datetime | None = None,
    ) -> KbDocument:
        """新建草稿文档（不进检索，需走审核发布才生效）。"""
        doc = await kb_document_repository.create(
            session,
            tenant_id=tenant_id,
            title=title,
            raw_content=raw_content,
            source_type=source_type,
            status="draft",
            effective_from=effective_from,
            expire_at=expire_at,
        )
        await self._add_version(session, tenant_id, doc, editor, note)
        self._audit(doc, "create", editor, note)
        await session.flush()
        return doc

    async def edit(
        self,
        session: AsyncSession,
        tenant_id: str,
        document_id: str,
        *,
        title: str | None = None,
        raw_content: str | None = None,
        editor: str | None = None,
        note: str | None = None,
    ) -> KbDocument:
        """编辑内容并生成新版本。

        编辑已发布文档：status 回到 draft（有未发布改动），但 published_version 不变——
        线上仍服务旧版本，直到再次审核发布。archived 文档需先回滚/重建，不可直接编辑。
        """
        doc = await self._get_owned(session, tenant_id, document_id)
        if doc.status == "archived":
            raise ValueError("已下线文档不可直接编辑，请先回滚到某个版本")
        if title is not None:
            doc.title = title
        if raw_content is not None:
            doc.raw_content = raw_content
        # 有新改动 → 回到草稿工作态（published_version 不动，线上不受影响）
        doc.status = "draft"
        await self._add_version(session, tenant_id, doc, editor, note)
        self._audit(doc, "edit", editor, note)
        await session.flush()
        return doc

    async def submit_review(
        self, session: AsyncSession, tenant_id: str, document_id: str, *, editor: str | None = None
    ) -> KbDocument:
        """提交审核（draft → pending_review）。"""
        doc = await self._get_owned(session, tenant_id, document_id)
        if doc.status != "draft":
            raise ValueError(f"只有草稿可提交审核（当前 {doc.status}）")
        doc.status = "pending_review"
        self._audit(doc, "submit", editor)
        await session.flush()
        return doc

    async def approve(
        self,
        session: AsyncSession,
        tenant_id: str,
        document_id: str,
        *,
        reviewer: str | None = None,
    ) -> dict[str, Any]:
        """审核通过并发布（pending_review → published）：重建分块与索引、切换线上版本。"""
        doc = await self._get_owned(session, tenant_id, document_id)
        if doc.status != "pending_review":
            raise ValueError(f"只有待审文档可通过（当前 {doc.status}）")
        return await self._publish(session, tenant_id, doc, reviewer, action="approve")

    async def reject(
        self,
        session: AsyncSession,
        tenant_id: str,
        document_id: str,
        *,
        reviewer: str | None = None,
        note: str | None = None,
    ) -> KbDocument:
        """驳回（pending_review → draft + 意见）。线上（若已发布过）不受影响。"""
        doc = await self._get_owned(session, tenant_id, document_id)
        if doc.status != "pending_review":
            raise ValueError(f"只有待审文档可驳回（当前 {doc.status}）")
        doc.status = "draft"
        self._audit(doc, "reject", reviewer, note)
        await session.flush()
        return doc

    async def archive(
        self, session: AsyncSession, tenant_id: str, document_id: str, *, editor: str | None = None
    ) -> KbDocument:
        """下线（→ archived）：立即退出检索（生效判据排除 archived），版本保留可回滚。"""
        doc = await self._get_owned(session, tenant_id, document_id)
        doc.status = "archived"
        self._audit(doc, "archive", editor)
        await session.flush()
        return doc

    async def rollback(
        self,
        session: AsyncSession,
        tenant_id: str,
        document_id: str,
        version: int,
        *,
        editor: str | None = None,
    ) -> dict[str, Any]:
        """回滚到历史版本并发布：把该版本内容记为新版本，重建索引切为线上。"""
        doc = await self._get_owned(session, tenant_id, document_id)
        ver = await kb_document_version_repository.get_version(
            session, tenant_id, document_id, version
        )
        if ver is None:
            raise ValueError(f"版本不存在：v{version}")
        doc.title = ver.title
        doc.raw_content = ver.raw_content
        doc.source_type = ver.source_type
        await self._add_version(session, tenant_id, doc, editor, note=f"回滚自 v{version}")
        return await self._publish(session, tenant_id, doc, editor, action=f"rollback:v{version}")

    async def list_versions(
        self, session: AsyncSession, tenant_id: str, document_id: str
    ) -> list[dict[str, Any]]:
        """版本历史（version 倒序）。"""
        rows = await kb_document_version_repository.list_by_document(
            session, tenant_id, document_id
        )
        return [
            {
                "version": r.version,
                "title": r.title,
                "editor": r.editor,
                "note": r.note,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]

    async def force_publish(
        self,
        session: AsyncSession,
        tenant_id: str,
        document_id: str,
        *,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """定时/强制发布（不校验当前 status，用于 kb_schedule 到点自动上线）。"""
        doc = await self._get_owned(session, tenant_id, document_id)
        return await self._publish(session, tenant_id, doc, actor, action="scheduled_publish")

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    @staticmethod
    async def _get_owned(
        session: AsyncSession, tenant_id: str, document_id: str
    ) -> KbDocument:
        """取文档并校验租户归属，不存在抛 ValueError。"""
        doc = await kb_document_repository.get_by_id_and_tenant(session, tenant_id, document_id)
        if doc is None:
            raise ValueError("文档不存在")
        return doc

    async def _publish(
        self,
        session: AsyncSession,
        tenant_id: str,
        doc: KbDocument,
        actor: str | None,
        *,
        action: str,
    ) -> dict[str, Any]:
        """发布当前 raw_content：复用 ingest 重建分块+索引，切换 published_version。"""
        # 待发布版本 = 当前最大版本号（每次编辑/回滚都已追加版本行）
        published_v = await kb_document_version_repository.next_version(
            session, tenant_id, doc.id
        ) - 1
        # 复用现有入库管线（整篇重建分块 + embedding + 建索引；UPDATE 分支置 status=published）；
        # 传入现有 metadata_json 防被清空（审计 review_log / 商品ID 等过滤维度都在里面）
        result = await kb_ingest_service.upsert_document(
            session,
            tenant_id,
            title=doc.title,
            raw_content=doc.raw_content,
            source_type=doc.source_type,
            document_id=doc.id,
            metadata=doc.metadata_json,
        )
        doc.published_version = published_v
        self._audit(doc, action, actor)
        await session.flush()
        # 语义缓存失效（Stage 17）：知识库发布后清该租户缓存，避免答旧内容；故障靠 TTL 兜底
        from app.chat.cache.semantic_cache import get_semantic_cache

        await get_semantic_cache().invalidate(tenant_id)
        return {**result, "published_version": published_v}

    async def _add_version(
        self,
        session: AsyncSession,
        tenant_id: str,
        doc: KbDocument,
        editor: str | None,
        note: str | None,
    ) -> int:
        """追加一个版本快照，返回版本号。"""
        v = await kb_document_version_repository.next_version(session, tenant_id, doc.id)
        await kb_document_version_repository.create(
            session,
            tenant_id=tenant_id,
            document_id=doc.id,
            version=v,
            title=doc.title,
            raw_content=doc.raw_content,
            source_type=doc.source_type,
            editor=editor,
            note=note,
        )
        return v

    @staticmethod
    def _audit(doc: KbDocument, action: str, actor: str | None, note: str | None = None) -> None:
        """审计落 metadata_json.review_log（谁/何时/动作/意见，保留最近 30 条）。"""
        meta = dict(doc.metadata_json or {})
        log = list(meta.get("review_log") or [])
        log.append(
            {
                "action": action,
                "actor": actor,
                "note": note,
                "at": datetime.now(timezone.utc).isoformat(),
            }
        )
        meta["review_log"] = log[-30:]
        doc.metadata_json = meta


# 模块级单例
kb_operations_service = KbOperationsService()
