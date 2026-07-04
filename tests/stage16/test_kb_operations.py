"""知识库运营编排测试（Stage 16，faked 向量后端，不依赖 Milvus）。"""

from datetime import datetime, timedelta, timezone

import pytest

from app.db.session import AsyncSessionLocal, dispose_engine
from app.repositories.kb_chunk_repository import kb_chunk_repository
from app.repositories.kb_document_repository import kb_document_repository
from app.services.kb_operations_service import kb_operations_service

TENANT = "kb-op-t"


class _FakeBackend:
    """no-op 向量后端：publish 走真实分块+embedding+PG，只跳过 Milvus 写。"""

    async def index_chunks(self, tenant_id, items):  # noqa: ANN001, D102
        return None

    async def index_faqs(self, tenant_id, items):  # noqa: ANN001, D102
        return None

    async def delete_document(self, tenant_id, document_id):  # noqa: ANN001, D102
        return None


@pytest.fixture(autouse=True)
def _fake_backend(monkeypatch):
    """publish 路径的向量后端换成 no-op（测试不连 Milvus）。"""
    monkeypatch.setattr("app.kb.ingest.get_vector_backend", lambda: _FakeBackend())


@pytest.fixture
async def _svc():
    yield
    await dispose_engine()


async def _is_live(session, doc_id: str) -> bool:
    """生效判据（与 retriever/list_active 一致）：published_version 非空且未 archived。"""
    docs = await kb_document_repository.list_active(session, TENANT)
    return any(d.id == doc_id for d in docs)


# ---------------------------------------------------------------------------
# 状态机全流程
# ---------------------------------------------------------------------------


async def test_full_lifecycle(_svc):
    content = "# 退换货政策\n\n七天内可无理由退货。"
    async with AsyncSessionLocal() as s:
        # 1. 草稿：不进检索
        doc = await kb_operations_service.create_draft(
            s, TENANT, title="退换货", raw_content=content, editor="alice"
        )
        doc_id = doc.id
        await s.commit()
        assert doc.status == "draft" and doc.published_version is None
        assert not await _is_live(s, doc_id)

        # 2. 提交审核
        d = await kb_operations_service.submit_review(s, TENANT, doc_id, editor="alice")
        await s.commit()
        assert d.status == "pending_review" and not await _is_live(s, doc_id)

        # 3. 通过发布 → 生效，published_version=1
        res = await kb_operations_service.approve(s, TENANT, doc_id, reviewer="bob")
        await s.commit()
        assert res["published_version"] == 1
        d = await kb_document_repository.get_by_id_and_tenant(s, TENANT, doc_id)
        assert d.status == "published" and d.published_version == 1
        assert await _is_live(s, doc_id)

        # 4. 编辑已发布文档 → 回到 draft，但 published_version 不变 → 线上仍生效（旧版本）
        d = await kb_operations_service.edit(
            s, TENANT, doc_id, raw_content=content + "\n\n定制商品除外。", editor="alice"
        )
        await s.commit()
        assert d.status == "draft" and d.published_version == 1
        assert await _is_live(s, doc_id)  # 关键：编辑不影响线上

        # 5. 再次审核发布 → 切到新版本
        d = await kb_operations_service.submit_review(s, TENANT, doc_id)
        res = await kb_operations_service.approve(s, TENANT, doc_id, reviewer="bob")
        await s.commit()
        assert res["published_version"] == 2
        assert await _is_live(s, doc_id)

        # 6. 版本历史有 2 条
        versions = await kb_operations_service.list_versions(s, TENANT, doc_id)
        assert [v["version"] for v in versions] == [2, 1]

        # 7. 回滚到 v1 → 内容变回、发布、published_version 前进
        res = await kb_operations_service.rollback(s, TENANT, doc_id, 1, editor="alice")
        await s.commit()
        d = await kb_document_repository.get_by_id_and_tenant(s, TENANT, doc_id)
        assert d.status == "published"
        assert "定制商品除外" not in d.raw_content  # 回到 v1 内容
        assert res["published_version"] == 3

        # 8. 下线 → 退出检索，版本仍在
        await kb_operations_service.archive(s, TENANT, doc_id, editor="bob")
        await s.commit()
        assert not await _is_live(s, doc_id)


async def test_reject_keeps_live(_svc):
    """驳回已发布文档的新草稿：状态回 draft，线上仍是旧版本。"""
    async with AsyncSessionLocal() as s:
        doc = await kb_operations_service.create_draft(
            s, TENANT, title="保修", raw_content="# 保修\n\n一年保修。"
        )
        doc_id = doc.id
        await kb_operations_service.submit_review(s, TENANT, doc_id)
        await kb_operations_service.approve(s, TENANT, doc_id)
        await s.commit()
        assert await _is_live(s, doc_id)
        # 编辑 + 提交 + 驳回
        await kb_operations_service.edit(s, TENANT, doc_id, raw_content="# 保修\n\n两年保修。")
        await kb_operations_service.submit_review(s, TENANT, doc_id)
        d = await kb_operations_service.reject(s, TENANT, doc_id, reviewer="bob", note="待核实")
        await s.commit()
        assert d.status == "draft" and await _is_live(s, doc_id)  # 线上仍旧版本


async def test_keyword_search_uses_stage16_live_predicate(_svc):
    """关键词召回应按 Stage 16 生效判据召回 published 文档。"""
    async with AsyncSessionLocal() as s:
        doc = await kb_operations_service.create_draft(
            s, TENANT, title="关键词召回", raw_content="# 售后暗号\n\n海盐芝士凭证可用于售后核验。"
        )
        doc_id = doc.id
        await kb_operations_service.submit_review(s, TENANT, doc_id)
        await kb_operations_service.approve(s, TENANT, doc_id)
        await s.commit()

        hits = await kb_chunk_repository.search_by_keywords(s, TENANT, ["海盐芝士"], limit=5)
        assert any(chunk.document_id == doc_id for chunk, _score in hits)


# ---------------------------------------------------------------------------
# 非法流转
# ---------------------------------------------------------------------------


async def test_illegal_transitions(_svc):
    async with AsyncSessionLocal() as s:
        doc = await kb_operations_service.create_draft(
            s, TENANT, title="x", raw_content="# x\n\ny"
        )
        doc_id = doc.id
        await s.commit()
        # 草稿不能直接 approve（未提交审核）
        with pytest.raises(ValueError):
            await kb_operations_service.approve(s, TENANT, doc_id)
        # 回滚不存在的版本
        with pytest.raises(ValueError):
            await kb_operations_service.rollback(s, TENANT, doc_id, 99)
        # 文档不存在
        with pytest.raises(ValueError):
            await kb_operations_service.submit_review(s, TENANT, "no-such-id")
        await s.rollback()


async def test_archived_cannot_edit(_svc):
    async with AsyncSessionLocal() as s:
        doc = await kb_operations_service.create_draft(
            s, TENANT, title="y", raw_content="# y\n\nz"
        )
        doc_id = doc.id
        await kb_operations_service.archive(s, TENANT, doc_id)
        with pytest.raises(ValueError):
            await kb_operations_service.edit(s, TENANT, doc_id, raw_content="# y\n\nnew")
        await s.rollback()


# ---------------------------------------------------------------------------
# 审计
# ---------------------------------------------------------------------------


async def test_audit_log(_svc):
    async with AsyncSessionLocal() as s:
        doc = await kb_operations_service.create_draft(
            s, TENANT, title="a", raw_content="# a\n\nb", editor="alice"
        )
        doc_id = doc.id
        await kb_operations_service.submit_review(s, TENANT, doc_id, editor="alice")
        await kb_operations_service.approve(s, TENANT, doc_id, reviewer="bob")
        await s.commit()
        d = await kb_document_repository.get_by_id_and_tenant(s, TENANT, doc_id)
        log = (d.metadata_json or {}).get("review_log") or []
        actions = [e["action"] for e in log]
        assert "create" in actions and "submit" in actions and "approve" in actions
        assert all(e.get("at") for e in log)  # 每条有时间戳


# ---------------------------------------------------------------------------
# 定时生效/失效
# ---------------------------------------------------------------------------


async def test_scheduled_publish_and_expire(_svc, monkeypatch):
    import scripts.kb_schedule as sched

    monkeypatch.setattr("app.kb.ingest.get_vector_backend", lambda: _FakeBackend())
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    async with AsyncSessionLocal() as s:
        # 到点生效：草稿 + effective_from 过去
        doc = await kb_operations_service.create_draft(
            s, TENANT, title="定时上线", raw_content="# t\n\ncontent", effective_from=past
        )
        eff_id = doc.id
        # 到点失效：已发布 + expire_at 过去
        doc2 = await kb_operations_service.create_draft(
            s, TENANT, title="定时下线", raw_content="# t2\n\nc2", expire_at=past
        )
        exp_id = doc2.id
        await kb_operations_service.submit_review(s, TENANT, exp_id)
        await kb_operations_service.approve(s, TENANT, exp_id)
        await s.commit()

    published = await sched.publish_effective()
    archived = await sched.archive_expired()
    assert published >= 1 and archived >= 1

    async with AsyncSessionLocal() as s:
        eff = await kb_document_repository.get_by_id_and_tenant(s, TENANT, eff_id)
        exp = await kb_document_repository.get_by_id_and_tenant(s, TENANT, exp_id)
        assert eff.published_version is not None and eff.effective_from is None  # 已上线+清标记
        assert exp.status == "archived" and exp.expire_at is None  # 已下线+清标记
        # 清理引擎
        await dispose_engine()
