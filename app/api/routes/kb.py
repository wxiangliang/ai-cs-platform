"""知识库管理 API（管理面）。

只做接参与调用 KbIngestService，不写业务逻辑。
临时保护：配置 KB_ADMIN_TOKEN 后需带 X-KB-Admin-Token 请求头
（Stage 08 统一鉴权前的过渡方案，见 stage-06 需求第 4 节）。
"""

from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthContext, require_admin, resolve_tenant_id
from app.core.exceptions import AppException
from app.core.responses import success_response
from app.db.session import get_db_session
from app.kb.ingest import kb_ingest_service
from app.kb.parsing.router import SUPPORTED_EXTENSIONS, DocumentParseError
from app.services.kb_operations_service import kb_operations_service

router = APIRouter(prefix="/api/kb", tags=["kb"])


class KbDocumentRequest(BaseModel):
    """创建/更新知识库文档请求。"""

    tenant_id: str | None = Field(default=None, max_length=64, description="鉴权开启后忽略")
    title: str = Field(..., min_length=1, max_length=256)
    content: str = Field(..., min_length=1, description="文档原文（支持 markdown/纯文本）")
    source_type: str = Field(default="policy", max_length=32)
    document_id: str | None = Field(default=None, description="传入则更新（整篇重建分块）")
    metadata: dict | None = None


class KbFaqRequest(BaseModel):
    """创建/更新 FAQ 请求。"""

    tenant_id: str | None = Field(default=None, max_length=64, description="鉴权开启后忽略")
    question: str = Field(..., min_length=1, max_length=512)
    answer: str = Field(..., min_length=1)
    category: str | None = Field(default=None, max_length=64)
    faq_id: str | None = None


@router.post("/documents")
async def upsert_document(
    body: KbDocumentRequest,
    auth: AuthContext | None = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """创建/更新知识库文档（清洗 → 分块 → embedding → 入库 → 建向量索引）。"""
    try:
        data = await kb_ingest_service.upsert_document(
            db,
            tenant_id=resolve_tenant_id(auth, body.tenant_id),
            title=body.title,
            raw_content=body.content,
            source_type=body.source_type,
            document_id=body.document_id,
            metadata=body.metadata,
        )
    except ValueError as exc:
        raise AppException(message=str(exc), error_code="KB_BAD_REQUEST", status_code=400) from exc
    return success_response(data=data)


@router.post("/documents/upload")
async def upload_document(
    auth: AuthContext | None = Depends(require_admin),
    tenant_id: str | None = Form(default=None, max_length=64),
    file: UploadFile = File(..., description=f"支持格式：{', '.join(SUPPORTED_EXTENSIONS)}"),
    title: str | None = Form(default=None, max_length=256),
    source_type: str = Form(default="policy", max_length=32),
    document_id: str | None = Form(default=None, description="传入则更新（整篇重建分块）"),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """上传文件入库：解析器链（MinerU/Docling/内置）→ 结构感知切分 → 建向量索引。"""
    content = await file.read()
    if not content:
        raise AppException(message="文件为空", error_code="KB_BAD_REQUEST", status_code=400)
    try:
        data = await kb_ingest_service.upsert_document_file(
            db,
            tenant_id=resolve_tenant_id(auth, tenant_id),
            file_name=file.filename or "unnamed",
            content=content,
            title=title,
            source_type=source_type,
            document_id=document_id,
        )
    except DocumentParseError as exc:
        raise AppException(message=str(exc), error_code="KB_PARSE_FAILED", status_code=422) from exc
    except ValueError as exc:
        raise AppException(message=str(exc), error_code="KB_BAD_REQUEST", status_code=400) from exc
    return success_response(data=data)


@router.delete("/documents/{document_id}")
async def disable_document(
    document_id: str,
    tenant_id: str | None = None,
    auth: AuthContext | None = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """停用文档（软删除：PG 标记 disabled + 删除向量索引）。"""
    try:
        await kb_ingest_service.disable_document(db, resolve_tenant_id(auth, tenant_id), document_id)
    except ValueError as exc:
        raise AppException(message=str(exc), error_code="KB_NOT_FOUND", status_code=404) from exc
    return success_response(data={"document_id": document_id, "status": "disabled"})


@router.post("/faqs")
async def upsert_faq(
    body: KbFaqRequest,
    auth: AuthContext | None = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """创建/更新 FAQ 标准问答。"""
    try:
        data = await kb_ingest_service.upsert_faq(
            db,
            tenant_id=resolve_tenant_id(auth, body.tenant_id),
            question=body.question,
            answer=body.answer,
            category=body.category,
            faq_id=body.faq_id,
        )
    except ValueError as exc:
        raise AppException(message=str(exc), error_code="KB_NOT_FOUND", status_code=404) from exc
    return success_response(data=data)


# —— Stage 16：文档运营（草稿-审核-发布状态机 + 版本回滚）——


class KbDraftRequest(BaseModel):
    """新建草稿请求。"""

    tenant_id: str | None = Field(default=None, max_length=64)
    title: str = Field(..., min_length=1, max_length=256)
    content: str = Field(..., min_length=1)
    source_type: str = Field(default="policy", max_length=32)
    editor: str | None = Field(default=None, max_length=64)
    note: str | None = None
    effective_from: datetime | None = None
    expire_at: datetime | None = None


class KbEditRequest(BaseModel):
    """编辑草稿/文档请求。"""

    tenant_id: str | None = Field(default=None, max_length=64)
    title: str | None = Field(default=None, max_length=256)
    content: str | None = None
    editor: str | None = Field(default=None, max_length=64)
    note: str | None = None


class KbActorRequest(BaseModel):
    """提交/通过/驳回/下线等动作（只需操作人 + 可选意见）。"""

    tenant_id: str | None = Field(default=None, max_length=64)
    actor: str | None = Field(default=None, max_length=64)
    note: str | None = None


class KbRollbackRequest(BaseModel):
    """回滚到指定版本。"""

    tenant_id: str | None = Field(default=None, max_length=64)
    version: int = Field(..., ge=1)
    actor: str | None = Field(default=None, max_length=64)


def _bad_request(exc: ValueError) -> AppException:
    return AppException(message=str(exc), error_code="KB_BAD_REQUEST", status_code=400)


@router.post("/documents/draft")
async def create_draft(
    body: KbDraftRequest,
    auth: AuthContext | None = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """新建草稿（不进检索，需审核发布才生效）。"""
    try:
        doc = await kb_operations_service.create_draft(
            db,
            resolve_tenant_id(auth, body.tenant_id),
            title=body.title,
            raw_content=body.content,
            source_type=body.source_type,
            editor=body.editor,
            note=body.note,
            effective_from=body.effective_from,
            expire_at=body.expire_at,
        )
    except ValueError as exc:
        raise _bad_request(exc) from exc
    return success_response(data={"document_id": doc.id, "status": doc.status})


@router.patch("/documents/{document_id}")
async def edit_document(
    document_id: str,
    body: KbEditRequest,
    auth: AuthContext | None = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """编辑内容并生成新版本（已发布文档编辑不影响线上，直到再次发布）。"""
    try:
        doc = await kb_operations_service.edit(
            db,
            resolve_tenant_id(auth, body.tenant_id),
            document_id,
            title=body.title,
            raw_content=body.content,
            editor=body.editor,
            note=body.note,
        )
    except ValueError as exc:
        raise _bad_request(exc) from exc
    return success_response(data={"document_id": doc.id, "status": doc.status})


@router.post("/documents/{document_id}/submit")
async def submit_review(
    document_id: str,
    body: KbActorRequest,
    auth: AuthContext | None = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """提交审核（draft → pending_review）。"""
    try:
        doc = await kb_operations_service.submit_review(
            db, resolve_tenant_id(auth, body.tenant_id), document_id, editor=body.actor
        )
    except ValueError as exc:
        raise _bad_request(exc) from exc
    return success_response(data={"document_id": doc.id, "status": doc.status})


@router.post("/documents/{document_id}/approve")
async def approve_document(
    document_id: str,
    body: KbActorRequest,
    auth: AuthContext | None = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """审核通过并发布（重建索引、切换线上版本）。"""
    try:
        data = await kb_operations_service.approve(
            db, resolve_tenant_id(auth, body.tenant_id), document_id, reviewer=body.actor
        )
    except ValueError as exc:
        raise _bad_request(exc) from exc
    return success_response(data=data)


@router.post("/documents/{document_id}/reject")
async def reject_document(
    document_id: str,
    body: KbActorRequest,
    auth: AuthContext | None = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """驳回（pending_review → draft + 意见）。"""
    try:
        doc = await kb_operations_service.reject(
            db, resolve_tenant_id(auth, body.tenant_id), document_id,
            reviewer=body.actor, note=body.note,
        )
    except ValueError as exc:
        raise _bad_request(exc) from exc
    return success_response(data={"document_id": doc.id, "status": doc.status})


@router.post("/documents/{document_id}/archive")
async def archive_document(
    document_id: str,
    body: KbActorRequest,
    auth: AuthContext | None = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """下线（→ archived，立即退出检索，版本保留可回滚）。"""
    try:
        doc = await kb_operations_service.archive(
            db, resolve_tenant_id(auth, body.tenant_id), document_id, editor=body.actor
        )
    except ValueError as exc:
        raise _bad_request(exc) from exc
    return success_response(data={"document_id": doc.id, "status": doc.status})


@router.post("/documents/{document_id}/rollback")
async def rollback_document(
    document_id: str,
    body: KbRollbackRequest,
    auth: AuthContext | None = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """回滚到历史版本并发布。"""
    try:
        data = await kb_operations_service.rollback(
            db, resolve_tenant_id(auth, body.tenant_id), document_id, body.version,
            editor=body.actor,
        )
    except ValueError as exc:
        raise _bad_request(exc) from exc
    return success_response(data=data)


@router.get("/documents/{document_id}/versions")
async def list_versions(
    document_id: str,
    tenant_id: str | None = None,
    auth: AuthContext | None = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """版本历史（version 倒序）。"""
    versions = await kb_operations_service.list_versions(
        db, resolve_tenant_id(auth, tenant_id), document_id
    )
    return success_response(data={"versions": versions})
